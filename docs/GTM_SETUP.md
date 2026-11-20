# Google Tag Manager + GA4 setup

Trip Check pushes events to the GTM `dataLayer`. GTM turns them into GA4 events. The `purchase` event
is different: the backend sends it to GA4 directly when Stripe's webhook confirms the payment.

**What gets sent:** never personal data. No emails, no names, no addresses. Only the event name and
the parameters in the table below.

## 0. The IDs you need

| Where | Setting | Looks like |
|---|---|---|
| GTM → Admin → Container | `NEXT_PUBLIC_GTM_ID` (frontend) | `GTM-XXXXXXX` |
| GA4 → Admin → Data streams → your web stream | `GA4_MEASUREMENT_ID` (backend) | `G-XXXXXXXXXX` |
| Same stream → **Measurement Protocol API secrets** → Create | `GA4_API_SECRET` (backend) | a random string |

Restart `npm run dev` after setting `NEXT_PUBLIC_GTM_ID`. On Vercel, set it and **redeploy**:
`NEXT_PUBLIC_` values are built into the page.

## 1. The events

| Event (dataLayer `event`) | When | Parameters |
|---|---|---|
| `check_started` | the user starts a check | `mode` (`fly`/`drive`/`auto`), `has_flights` |
| `check_completed` | the first answer arrives | `mode`, `risk_level`, `ms_to_first_answer` |
| `paywall_shown` | the paywall card appears | `reason` (`free_limit_reached` / `subscription_required`) |
| `sign_up` | an account was created | `method` (`email`/`google`) |
| `login` | the user signed in | `method` |
| `begin_checkout` | the Upgrade button is clicked | `value` (4.99), `currency` (USD), `items` |
| `purchase` | **server-side**, from the Stripe webhook | `transaction_id`, `value`, `currency`, `items` |

## 2. In GTM: the Google tag

1. tagmanager.google.com → your container → **Tags** → **New**.
2. Tag configuration → **Google tag**. Tag ID: your `G-...` Measurement ID.
3. Triggering → **Initialization - All Pages**. Save as "Google tag - GA4".

## 3. In GTM: the variables (one per parameter)

**Variables** → User-Defined Variables → **New** → **Data Layer Variable**, once for each name below.
Use the same name for the variable and the "Data Layer Variable Name", with Version 2:

`mode`, `has_flights`, `risk_level`, `ms_to_first_answer`, `reason`, `method`, `value`, `currency`, `items`

Name them `DLV - mode`, `DLV - has_flights` and so on.

## 4. In GTM: one trigger + one tag per event

Do this for each of `check_started`, `check_completed`, `paywall_shown`, `sign_up`, `login`, `begin_checkout`:

1. **Triggers** → New → **Custom Event**. Event name: exactly the event name (for example `check_started`).
   Fires on: All Custom Events. Save as "CE - check_started".
2. **Tags** → New → **Google Analytics: GA4 Event**.
   - Measurement ID: your `G-...` ID.
   - Event Name: the same event name.
   - Event Parameters: add the parameters for that event from the table in section 1,
     with the matching variable as the value (for example `mode` → `{{DLV - mode}}`).
   - For `begin_checkout`: under **More settings → Ecommerce**, tick "Send Ecommerce data" →
     Data source: Data Layer. Also add `value` and `currency` as parameters.
   - Triggering: the trigger from step 1.
   - Save as "GA4 - check_started", and so on.

Don't create a GTM tag for `purchase`: the backend sends it.

Click **Submit** → **Publish**.

## 5. In GA4: custom dimensions (so the parameters show in reports)

GA4 → Admin → **Custom definitions** → Create custom dimension, scope **Event**, for: `mode`,
`has_flights`, `risk_level`, `reason`, `method`. Create a custom **metric** for `ms_to_first_answer`
(unit: milliseconds).

## 6. Check it works

**GTM Preview:** in GTM click **Preview**, enter `http://localhost:3000`, and run a check. The Tag
Assistant window lists `check_started`, then `check_completed`, with "GA4 - ..." under *Tags Fired*.
Run a second air check for `paywall_shown`, then sign up (`sign_up`) and click Upgrade (`begin_checkout`).

**GA4 DebugView:** GA4 → Admin → **DebugView**. Events from GTM Preview show up there within a few
seconds. To see the server-side `purchase` in DebugView too, temporarily send it to the validation
server or add `"debug_mode": 1` to its params in `backend/app/ga4.py`. Otherwise it appears in the
**Realtime** report within a minute, and in normal reports after up to 24 hours.

## 7. The sign-up funnel

GA4 → **Explore** → **Funnel exploration**. Steps (each one "Event name exactly matches"):

1. `check_started`
2. `paywall_shown`
3. `sign_up`
4. `begin_checkout`
5. `purchase`

Turn on "Make open funnel" off (closed funnel), and break down by `mode` or by device category.
This shows where people drop out between hitting the limit and paying.

## Why `purchase` is sent from the server

- **It's always right.** The webhook runs only after Stripe confirms the payment. A browser event
  can be lost (tab closed, ad blocker) or faked (opening the success page without paying).
- **It's the same visitor.** At checkout the page sends GA4's client ID (from the `_ga` cookie) to the
  backend, which stores it on the Stripe session. The webhook sends `purchase` with that client ID,
  so GA4 joins the purchase to the same visit as `begin_checkout`.
