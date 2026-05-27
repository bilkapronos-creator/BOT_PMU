"""Facturation Stripe + profils Premium Supabase (service_role côté API Render)."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Optional

from velora_resilience import ArchivesStorageError, options_client_supabase, supabase_execute

_PROFILES_TABLE = "profiles"
_USAGE_TABLE = "velora_usage_daily"
_client = None

QUOTA_JOURNALIER = int(os.environ.get("VELORA_DAILY_ANALYSIS_LIMIT", "3"))


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
    return _client


def _stripe_configure() -> None:
    import stripe

    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not secret:
        raise BillingConfigError("STRIPE_SECRET_KEY non configurée sur Render.")
    stripe.api_key = secret


def obtenir_profil(user_id: str) -> Optional[dict[str, Any]]:
    client = _get_supabase_client()

    def _lire(cols: str):
        return (
            client.table(_PROFILES_TABLE)
            .select(cols)
            .eq("id", user_id)
            .limit(1)
            .execute()
        )

    for cols in (
        "id, role, plan_type, is_premium, stripe_customer_id",
        "id, role, plan_type",
    ):
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


def est_utilisateur_premium(profil: Optional[dict[str, Any]]) -> bool:
    if not profil:
        return False
    if profil.get("is_premium") is True:
        return True
    role = str(profil.get("role") or "").lower()
    return role in ("premium", "admin")


def consommer_slot_analyse(user_id: str, daily_limit: int = QUOTA_JOURNALIER) -> dict[str, Any]:
    """Incrémente le quota journalier sauf membre Premium / admin."""
    profil = obtenir_profil(user_id)
    if est_utilisateur_premium(profil):
        return {
            "allowed": True,
            "unlimited": True,
            "is_premium": True,
            "role": profil.get("role") if profil else "premium",
        }

    client = _get_supabase_client()
    today = date.today().isoformat()

    def _lire_usage():
        return (
            client.table(_USAGE_TABLE)
            .select("analysis_count")
            .eq("user_id", user_id)
            .eq("usage_date", today)
            .limit(1)
            .execute()
        )

    resp = supabase_execute(_lire_usage, description="lecture quota journalier")
    rows = resp.data or []
    count = int(rows[0].get("analysis_count") or 0) if rows else 0

    if count >= daily_limit:
        raise QuotaExceededError(count, daily_limit)

    new_count = count + 1

    try:
        def _ecrire_usage():
            if rows:
                return (
                    client.table(_USAGE_TABLE)
                    .update({"analysis_count": new_count, "updated_at": _now_iso()})
                    .eq("user_id", user_id)
                    .eq("usage_date", today)
                    .execute()
                )
            payload: dict[str, Any] = {
                "user_id": user_id,
                "usage_date": today,
                "analysis_count": new_count,
            }
            try:
                payload["updated_at"] = _now_iso()
            except Exception:
                pass
            return client.table(_USAGE_TABLE).insert(payload).execute()

        supabase_execute(_ecrire_usage, description="incrément quota journalier")
    except Exception as exc:
        print(f"[Velora] Écriture quota ignorée : {exc}")
        return {
            "allowed": True,
            "unlimited": False,
            "is_premium": False,
            "quota_degraded": True,
        }

    return {
        "allowed": True,
        "unlimited": False,
        "is_premium": False,
        "used_today": new_count,
        "daily_limit": daily_limit,
        "remaining": max(0, daily_limit - new_count),
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
