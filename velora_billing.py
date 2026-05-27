"""Facturation Stripe + profils Premium Supabase (service_role côté API Render)."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx

from velora_resilience import ArchivesStorageError, options_client_supabase, supabase_execute

_PROFILES_TABLE = "profiles"
_client = None

QUOTA_JOURNALIER = int(os.environ.get("VELORA_DAILY_ANALYSIS_LIMIT", "3"))
_COLONNES_PROFIL = (
    "id, role, plan_type, is_premium, stripe_customer_id, analyses_count, last_analysis_date",
    "id, role, plan_type, is_premium, stripe_customer_id",
    "id, role, plan_type",
)


class BillingConfigError(RuntimeError):
    """Configuration Stripe ou Supabase manquante."""


class QuotaExceededError(Exception):
    """Quota d'analyses journalier épuisé."""

    def __init__(self, used: int, daily_limit: int):
        self.used = used
        self.daily_limit = daily_limit
        super().__init__(f"Quota atteint ({used}/{daily_limit})")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_supabase_client():
    global _client
    if _client is not None:
        return _client

    url = (os.environ.get("SUPABASE_URL") or "").strip().replace("/rest/v1", "").rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise BillingConfigError(
            "SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY requis pour la facturation.",
        )

    try:
        from supabase import create_client
    except ImportError as exc:
        raise BillingConfigError("Paquet « supabase » manquant.") from exc

    options = options_client_supabase()
    _client = create_client(url, key, options) if options else create_client(url, key)
    print(f"[Velora] Client Supabase billing initialisé (service_role, url={url[:40]}…)")
    return _client


def _credentials_service_role() -> tuple[str, str]:
    """URL PostgREST + clé service_role (contourne RLS)."""
    url = (os.environ.get("SUPABASE_URL") or "").strip().replace("/rest/v1", "").rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise BillingConfigError(
            "SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY requis sur Render pour le quota.",
        )
    return url, key


def _headers_service_role() -> dict[str, str]:
    _, key = _credentials_service_role()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _patch_profil_rest(user_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """PATCH PostgREST direct avec service_role — bypass RLS garanti."""
    base_url, _key = _credentials_service_role()
    url = f"{base_url}/rest/v1/{_PROFILES_TABLE}"
    uid = str(user_id).strip()

    response = httpx.patch(
        url,
        params={"id": f"eq.{uid}"},
        headers=_headers_service_role(),
        json=payload,
        timeout=35.0,
    )

    if response.status_code >= 400:
        raise ArchivesStorageError(
            f"PATCH profiles échoué ({response.status_code}) : {response.text[:400]}",
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise ArchivesStorageError("PATCH profiles : réponse JSON invalide.") from exc

    if not isinstance(data, list) or len(data) == 0:
        raise ArchivesStorageError(
            f"PATCH profiles : 0 ligne mise à jour pour {uid} (vérifiez l'UUID / auth.users).",
        )

    return data


def _stripe_configure() -> None:
    import stripe

    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not secret:
        raise BillingConfigError("STRIPE_SECRET_KEY non configurée sur Render.")
    stripe.api_key = secret


def obtenir_profil(user_id: str) -> Optional[dict[str, Any]]:
    try:
        client = _get_supabase_client()
    except BillingConfigError as exc:
        print(f"[Velora] Lecture profil sans client Supabase : {exc}")
        return None

    def _lire(cols: str):
        return (
            client.table(_PROFILES_TABLE)
            .select(cols)
            .eq("id", user_id)
            .limit(1)
            .execute()
        )

    for cols in _COLONNES_PROFIL:
        try:
            resp = supabase_execute(
                lambda c=cols: _lire(c),
                description=f"lecture profil ({cols})",
            )
            rows = resp.data or []
            if rows:
                return rows[0]
        except Exception as exc:
            print(f"[Velora] Lecture profil ({cols}) : {exc}")
    return None


def _profil_depuis_rpc(data: Any, default: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(data, dict) or not data.get("ok"):
        reason = data.get("reason") if isinstance(data, dict) else data
        print(f"[Velora] ensure_velora_profile échec : {reason}")
        return None
    return {
        **default,
        "id": data.get("id", default["id"]),
        "role": data.get("role", "free"),
        "plan_type": data.get("plan_type", "free"),
        "is_premium": data.get("is_premium", False),
        "analyses_count": data.get("analyses_count", 0),
        "last_analysis_date": data.get("last_analysis_date"),
    }


def _aujourdhui() -> date:
    return datetime.now(timezone.utc).date()


def _normaliser_profil_lu(profil: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Normalise une ligne profiles lue en base (sans écraser le compteur par défaut)."""
    return {
        "id": profil.get("id", user_id),
        "role": profil.get("role", "free"),
        "plan_type": profil.get("plan_type", "free"),
        "is_premium": profil.get("is_premium") is True,
        "stripe_customer_id": profil.get("stripe_customer_id"),
        "analyses_count": int(profil.get("analyses_count") or 0),
        "last_analysis_date": profil.get("last_analysis_date"),
    }


def _lire_compteur_supabase(user_id: str) -> dict[str, Any]:
    """Lit analyses_count / last_analysis_date depuis Supabase (source de vérité quota)."""
    client = _get_supabase_client()
    uid = str(user_id).strip()

    def _lire():
        return (
            client.table(_PROFILES_TABLE)
            .select("id, role, plan_type, is_premium, analyses_count, last_analysis_date")
            .eq("id", uid)
            .limit(1)
            .execute()
        )

    try:
        resp = supabase_execute(_lire, description="lecture compteur quota")
    except Exception as exc:
        raise BillingConfigError(
            f"Lecture quota impossible ({uid}) : {exc}. "
            "Exécutez supabase/profiles_quota.sql et vérifiez SUPABASE_SERVICE_ROLE_KEY.",
        ) from exc

    rows = resp.data or []
    if not rows:
        raise BillingConfigError(f"Profil quota introuvable en base pour {uid}.")

    if "analyses_count" not in rows[0]:
        raise BillingConfigError(
            "Colonne analyses_count absente. Exécutez supabase/profiles_quota.sql.",
        )

    return _normaliser_profil_lu(rows[0], uid)


def _profil_defaut_dict(user_id: str) -> dict[str, Any]:
    """Valeurs par défaut si la ligne profiles n'existe pas encore en base."""
    today = _aujourdhui()
    return {
        "id": str(user_id).strip(),
        "role": "free",
        "plan_type": "free",
        "is_premium": False,
        "analyses_count": 0,
        "last_analysis_date": today.isoformat(),
    }


def _est_erreur_ligne_existe(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "duplicate" in msg or "23505" in msg or "already exists" in msg


def creer_profil_par_defaut(user_id: str) -> dict[str, Any]:
    """Crée un profil free via RPC Supabase — ne bloque jamais l'analyse."""
    uid = str(user_id).strip()
    default = _profil_defaut_dict(uid)
    today = default["last_analysis_date"]
    now = _now_iso()

    try:
        client = _get_supabase_client()
    except BillingConfigError as exc:
        print(f"[Velora] service_role indisponible, profil synthétique pour {uid} : {exc}")
        return default

    # 1) RPC SECURITY DEFINER (contourne RLS, vérifie auth.users)
    try:
        def _rpc():
            return client.rpc("ensure_velora_profile", {"p_user_id": uid}).execute()

        resp = supabase_execute(_rpc, description="RPC ensure_velora_profile")
        if _profil_depuis_rpc(resp.data, default) is not None:
            profil = obtenir_profil(uid)
            if profil:
                return {**default, **profil}
            return _profil_depuis_rpc(resp.data, default) or default
    except Exception as exc:
        print(f"[Velora] RPC ensure_velora_profile {uid} : {exc}")

    # 2) Insert direct (secours)
    payloads: list[dict[str, Any]] = [
        {"id": uid, "role": "free", "plan_type": "free"},
        {
            "id": uid,
            "role": "free",
            "plan_type": "free",
            "is_premium": False,
            "analyses_count": 0,
            "last_analysis_date": today,
            "updated_at": now,
        },
    ]

    insert_ok = False
    for payload in payloads:
        try:
            def _insert(p=payload):
                return client.table(_PROFILES_TABLE).insert(p).execute()

            supabase_execute(_insert, description="insert profil par défaut")
            insert_ok = True
            break
        except Exception as exc:
            if _est_erreur_ligne_existe(exc):
                insert_ok = True
                break
            print(f"[Velora] Insert profil {list(payload.keys())} : {exc}")

    if insert_ok:
        profil = obtenir_profil(uid)
        if profil:
            return {**default, **profil}

    try:
        def _patch():
            return (
                client.table(_PROFILES_TABLE)
                .update(
                    {
                        "analyses_count": 0,
                        "last_analysis_date": today,
                        "updated_at": now,
                    },
                )
                .eq("id", uid)
                .execute()
            )

        supabase_execute(_patch, description="patch quota profil")
        profil = obtenir_profil(uid)
        if profil:
            return {**default, **profil}
    except Exception as exc:
        print(f"[Velora] Patch quota profil {uid} : {exc}")

    print(f"[Velora] Profil {uid} : profil synthétique (analyse autorisée).")
    return default


def obtenir_ou_creer_profil(user_id: str) -> dict[str, Any]:
    """Lit le profil ou le crée à la volée."""
    uid = str(user_id).strip()
    profil = obtenir_profil(uid)
    if profil:
        return _normaliser_profil_lu(profil, uid)
    print(f"[Velora] Profil absent pour {uid} — création automatique.")
    created = creer_profil_par_defaut(uid)
    try:
        return _lire_compteur_supabase(uid)
    except BillingConfigError:
        return _normaliser_profil_lu(created, uid)


def est_utilisateur_premium(profil: Optional[dict[str, Any]]) -> bool:
    if not profil:
        return False
    if profil.get("is_premium") is True:
        return True
    role = str(profil.get("role") or "").lower()
    return role in ("premium", "admin")


def _parser_date_profil(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    texte = str(value).strip()[:10]
    try:
        return date.fromisoformat(texte)
    except ValueError:
        return None


def _reinitialiser_compteur_jour(client, user_id: str, today: date) -> None:
    def _maj():
        return (
            client.table(_PROFILES_TABLE)
            .update(
                {
                    "analyses_count": 0,
                    "last_analysis_date": today.isoformat(),
                    "updated_at": _now_iso(),
                },
            )
            .eq("id", user_id)
            .execute()
        )

    supabase_execute(_maj, description="réinitialisation quota journalier")


def verifier_quota_analyse(user_id: str, daily_limit: int = QUOTA_JOURNALIER) -> dict[str, Any]:
    """
    Vérifie le quota AVANT analyse (profiles.analyses_count / last_analysis_date).
    Premium → illimité. Sinon blocage strict à daily_limit analyses/jour.
    """
    obtenir_ou_creer_profil(user_id)
    row = _lire_compteur_supabase(user_id)

    if est_utilisateur_premium(row):
        return {"allowed": True, "unlimited": True, "is_premium": True}

    today = _aujourdhui()
    last_date = _parser_date_profil(row.get("last_analysis_date"))
    count = int(row.get("analyses_count") or 0)

    if last_date != today:
        count = 0
        client = _get_supabase_client()
        _reinitialiser_compteur_jour(client, user_id, today)

    print(f"[Velora] Quota check {user_id} : {count}/{daily_limit} (premium={row.get('is_premium')})")

    if count >= daily_limit:
        raise QuotaExceededError(count, daily_limit)

    return {
        "allowed": True,
        "unlimited": False,
        "is_premium": False,
        "used_today": count,
        "daily_limit": daily_limit,
        "remaining": max(0, daily_limit - count),
    }


def incrementer_compteur_analyse(user_id: str) -> None:
    """
    Incrémente analyses_count via service_role (RPC SECURITY DEFINER ou PATCH REST).
    Lève ArchivesStorageError si la persistance échoue.
    """
    uid = str(user_id).strip()
    row = _lire_compteur_supabase(uid)

    if est_utilisateur_premium(row):
        print(f"[Velora] Incrément ignoré (Premium) : {uid}")
        return

    today = _aujourdhui()
    last_date = _parser_date_profil(row.get("last_analysis_date"))
    count = int(row.get("analyses_count") or 0)
    if last_date != today:
        count = 0

    nouvelle_valeur = count + 1
    payload = {
        "analyses_count": nouvelle_valeur,
        "last_analysis_date": today.isoformat(),
        "updated_at": _now_iso(),
    }

    # 1) RPC atomique (recommandé — exécuter supabase/increment_analysis_count.sql)
    try:
        client = _get_supabase_client()

        def _rpc():
            return client.rpc("increment_velora_analysis_count", {"p_user_id": uid}).execute()

        resp = supabase_execute(_rpc, description="RPC increment_velora_analysis_count")
        data = resp.data
        if isinstance(data, dict) and data.get("ok") is True:
            if data.get("skipped"):
                return
            persisted = int(data.get("analyses_count") or nouvelle_valeur)
            print(f"[Velora] Incrémentation réussie (RPC) : {uid} → {persisted}")
            return
        if isinstance(data, dict) and data.get("ok") is False:
            print(f"[Velora] RPC increment échec : {data.get('reason')}")
    except Exception as exc:
        print(f"[Velora] RPC increment_velora_analysis_count : {exc}")

    # 2) PATCH REST direct service_role (contourne RLS PostgREST)
    try:
        rows = _patch_profil_rest(uid, payload)
        persisted = int(rows[0].get("analyses_count") or nouvelle_valeur)
        if persisted != nouvelle_valeur:
            raise ArchivesStorageError(
                f"Valeur persistée inattendue pour {uid} : {persisted} (attendu {nouvelle_valeur}).",
            )
        print(f"[Velora] Incrémentation réussie (REST) : {uid} → {persisted}")
        return
    except ArchivesStorageError:
        raise
    except Exception as exc:
        raise ArchivesStorageError(f"PATCH REST increment échoué pour {uid} : {exc}") from exc


def consommer_slot_analyse(user_id: str, daily_limit: int = QUOTA_JOURNALIER) -> dict[str, Any]:
    """Alias legacy : vérifie puis incrémente (préférer verifier + incrementer séparés)."""
    stats = verifier_quota_analyse(user_id, daily_limit)
    if stats.get("unlimited"):
        return stats
    incrementer_compteur_analyse(user_id)
    used = int(stats.get("used_today") or 0) + 1
    return {
        **stats,
        "used_today": used,
        "remaining": max(0, daily_limit - used),
    }


def obtenir_profil_par_stripe_customer(stripe_customer_id: str) -> Optional[dict[str, Any]]:
    """Retrouve un profil via l'ID client Stripe (cus_…)."""
    cid = str(stripe_customer_id or "").strip()
    if not cid:
        return None

    client = _get_supabase_client()

    def _lire():
        return (
            client.table(_PROFILES_TABLE)
            .select("id, role, plan_type, is_premium, stripe_customer_id")
            .eq("stripe_customer_id", cid)
            .limit(1)
            .execute()
        )

    resp = supabase_execute(_lire, description="lecture profil par stripe_customer_id")
    rows = resp.data or []
    return rows[0] if rows else None


def activer_premium_stripe(user_id: str, stripe_customer_id: Optional[str] = None) -> None:
    """Webhook Stripe : passage Premium (service_role, bypass RLS)."""
    client = _get_supabase_client()
    payload: dict[str, Any] = {
        "is_premium": True,
        "role": "premium",
        "plan_type": "premium",
        "updated_at": _now_iso(),
    }
    if stripe_customer_id:
        payload["stripe_customer_id"] = stripe_customer_id

    def _maj():
        return client.table(_PROFILES_TABLE).update(payload).eq("id", user_id).execute()

    supabase_execute(_maj, description="activation Premium Stripe")


def desactiver_premium_stripe(
    *,
    user_id: Optional[str] = None,
    stripe_customer_id: Optional[str] = None,
) -> Optional[str]:
    """
    Webhook Stripe : fin d'abonnement → repasse en Free.
    Ne touche pas aux comptes admin.
    """
    profil = None
    if user_id:
        profil = obtenir_profil(str(user_id))
    elif stripe_customer_id:
        profil = obtenir_profil_par_stripe_customer(str(stripe_customer_id))

    if not profil:
        return None

    uid = str(profil["id"])
    if str(profil.get("role") or "").lower() == "admin":
        return uid

    client = _get_supabase_client()
    payload: dict[str, Any] = {
        "is_premium": False,
        "role": "free",
        "plan_type": "free",
        "updated_at": _now_iso(),
    }

    def _maj():
        return client.table(_PROFILES_TABLE).update(payload).eq("id", uid).execute()

    supabase_execute(_maj, description="désactivation Premium Stripe")
    return uid


def creer_session_checkout_stripe(
    user_id: str,
    *,
    success_url: str,
    cancel_url: str,
    customer_email: Optional[str] = None,
) -> dict[str, str]:
    """Crée une session Checkout Stripe (mode abonnement test)."""
    _stripe_configure()
    import stripe

    price_id = (os.environ.get("STRIPE_PRICE_ID") or "").strip()
    if not price_id:
        raise BillingConfigError("STRIPE_PRICE_ID non configurée sur Render.")

    profil = obtenir_profil(user_id)
    params: dict[str, Any] = {
        "mode": "subscription",
        "client_reference_id": user_id,
        "metadata": {"user_id": user_id},
        "subscription_data": {"metadata": {"user_id": user_id}},
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": True,
    }

    stripe_customer_id = (profil or {}).get("stripe_customer_id")
    if stripe_customer_id:
        params["customer"] = stripe_customer_id
    elif customer_email:
        params["customer_email"] = customer_email

    session = stripe.checkout.Session.create(**params)
    if not session.url:
        raise BillingConfigError("Stripe n'a pas renvoyé d'URL de paiement.")

    return {"url": session.url, "session_id": session.id}


def traiter_webhook_stripe(payload: bytes, signature: Optional[str]) -> dict[str, str]:
    """Vérifie la signature Stripe et met à jour le statut Premium."""
    _stripe_configure()
    import stripe

    webhook_secret = (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not webhook_secret:
        raise BillingConfigError("STRIPE_WEBHOOK_SECRET non configurée sur Render.")
    if not signature:
        raise ValueError("En-tête Stripe-Signature manquant.")

    try:
        event = stripe.Webhook.construct_event(payload, signature, webhook_secret)
    except ValueError as exc:
        raise ValueError("Payload webhook invalide.") from exc
    except stripe.error.SignatureVerificationError as exc:
        raise ValueError("Signature webhook Stripe invalide.") from exc

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        user_id = (
            (obj.get("metadata") or {}).get("user_id")
            or obj.get("client_reference_id")
        )
        if not user_id:
            raise ValueError("user_id absent de la session Stripe.")

        stripe_customer_id = obj.get("customer")
        if isinstance(stripe_customer_id, dict):
            stripe_customer_id = stripe_customer_id.get("id")

        activer_premium_stripe(str(user_id), str(stripe_customer_id) if stripe_customer_id else None)
        return {"status": "premium_activated", "user_id": str(user_id)}

    if event_type == "customer.subscription.deleted":
        stripe_customer_id = obj.get("customer")
        if isinstance(stripe_customer_id, dict):
            stripe_customer_id = stripe_customer_id.get("id")

        metadata_user_id = (obj.get("metadata") or {}).get("user_id")
        uid = desactiver_premium_stripe(
            user_id=str(metadata_user_id) if metadata_user_id else None,
            stripe_customer_id=str(stripe_customer_id) if stripe_customer_id else None,
        )
        if not uid:
            return {
                "status": "ignored",
                "type": event_type,
                "reason": "profile_not_found",
            }
        return {"status": "premium_deactivated", "user_id": uid}

    return {"status": "ignored", "type": event_type}
