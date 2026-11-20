# Sign-in setup: password reset and Google

## Password reset (works out of the box)

1. The sign-in page has **Forgot your password?** → `/forgot-password`.
2. The user enters their email. Supabase sends a link (the same answer is shown whether or not the
   email has an account, so nobody can find out who has signed up).
3. The link opens `/reset-password`, already signed in for that purpose. The user picks a new password.

**Supabase settings:** Authentication → **URL Configuration**:
- **Site URL:** `http://localhost:3000` locally; your Vercel URL in production.
- **Redirect URLs:** add `http://localhost:3000/**` and `https://<your-app>.vercel.app/**`.
  If they're missing, Supabase sends the reset link to the home page instead. The home page passes it
  on to `/reset-password`, so it still works, but it's tidier to add them.

**Emails:** Supabase's built-in email service is for testing and sends only a few emails per hour.
For real users, set up your own SMTP (Authentication → Emails → SMTP Settings), e.g. Resend or SendGrid.

## Continue with Google (sign-up and sign-in)

One button does both: the first time someone uses it, Supabase creates their account.

### 1. Google Cloud: create the OAuth client

1. Go to **console.cloud.google.com** → pick or create a project.
2. **APIs & Services → OAuth consent screen** (or "Google Auth Platform"): choose **External**,
   app name "Trip Check", your email as support and developer contact. Save.
   While it is in **Testing**, add your own Google account under **Test users**.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - **Authorized JavaScript origins:** `http://localhost:3000` (and your Vercel URL later)
   - **Authorized redirect URIs:** `https://qolqqoyiacyhritczyge.supabase.co/auth/v1/callback`
4. Create, then copy the **Client ID** and **Client secret**.

### 2. Supabase: switch Google on

Authentication → **Sign In / Providers** → **Google** → enable → paste the Client ID and Client
secret → Save. (The callback URL shown there should match the redirect URI above.)

### 3. Trip Check: show the button

In `frontend/.env.local` set `NEXT_PUBLIC_ENABLE_GOOGLE_AUTH=true`, then restart `npm run dev`
(on Vercel: set it and redeploy). **Continue with Google** appears on the Sign in and Create
account pages.
