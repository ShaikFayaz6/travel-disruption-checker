"""Creates the Trip Check Pro product and its $4.99/month price in Stripe TEST mode. Safe to run again.

    cd backend && python scripts/stripe_setup.py      (or: python -m scripts.stripe_setup)

Needs STRIPE_SECRET_KEY (sk_test_...) in backend/.env. Prints the price ID to put in STRIPE_PRICE_ID.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `app` imports when run as a file

import stripe

from app.config import STRIPE_SECRET_KEY

PRODUCT_NAME = "Trip Check Pro"
LOOKUP_KEY = "tripcheck_pro_monthly"
AMOUNT_CENTS = 499


def main() -> None:
    if not STRIPE_SECRET_KEY:
        sys.exit("Set STRIPE_SECRET_KEY=sk_test_... in backend/.env first.")
    if not STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_")):
        sys.exit("Refusing to run: this script is for Stripe test mode only (use an sk_test_ key).")
    stripe.api_key = STRIPE_SECRET_KEY

    found = stripe.Price.list(lookup_keys=[LOOKUP_KEY], limit=1).data
    if found:
        price = found[0]
        print(f"Price already exists ({price.unit_amount / 100:.2f} {price.currency.upper()}/month).")
    else:
        product = next((p for p in stripe.Product.list(active=True, limit=100).auto_paging_iter()
                        if p.name == PRODUCT_NAME), None)
        if product is None:
            product = stripe.Product.create(name=PRODUCT_NAME,
                                            description="Unlimited air checks on Trip Check.")
            print(f"Created product {product.id}")
        price = stripe.Price.create(product=product.id, unit_amount=AMOUNT_CENTS, currency="usd",
                                    recurring={"interval": "month"}, lookup_key=LOOKUP_KEY,
                                    nickname="Trip Check Pro monthly")
        print(f"Created price {price.id}")
    print(f"\nPut this in backend/.env:\n  STRIPE_PRICE_ID={price.id}")


if __name__ == "__main__":
    main()
