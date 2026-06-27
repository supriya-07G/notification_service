# Google SSO Setup

Follow these steps once to enable "Sign in with Google" on the login page.

---

## 1. Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or select your existing EcoSave project)
3. In the left menu: **APIs & Services → OAuth consent screen**

---

## 2. Configure the OAuth Consent Screen

- **User type:** Internal *(limits sign-in to your Google Workspace domain)*
- **App name:** EcoSave Notifications
- **User support email:** michael@ecosave-group.com
- **Scopes:** add `email`, `profile`, `openid`
- Save and continue through all steps

---

## 3. Create OAuth Credentials

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. **Application type:** Web application
3. **Name:** EcoSave Notification Service
4. **Authorised redirect URIs** — add exactly:
   ```
   https://spark-1bc3.tailea79dc.ts.net/auth/google/callback
   ```
   *(If testing locally, also add `http://localhost:8096/auth/google/callback`)*
5. Click **Create**
6. Copy the **Client ID** and **Client Secret**

---

## 4. Add Credentials to `.env`

```env
GOOGLE_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_client_secret_here
```

---

## 5. Restart the Server

```bash
sudo systemctl restart ecosave-webhooks
```

The migration runs automatically on startup and creates the `oauth_states` table.

---

## 6. Verify It Works

1. Open `https://spark-1bc3.tailea79dc.ts.net/dashboard/login`
2. Click **Sign in with Google**
3. Select your `@ecosave-group.com` Google account
4. You should land on the dashboard

---

## Security Notes

- Only `@ecosave-group.com` accounts are accepted — any other domain gets rejected
- The OAuth `state` parameter is stored in the DB and expires after 5 minutes (CSRF protection)
- New staff are auto-created with `role = staff`. Promote to `admin` via the Staff page if needed.
- Rate limiting (5 attempts / 15 min per IP) applies to SSO the same as password login
- To disable SSO: remove `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` from `.env` — the button still appears but clicking it shows "Google SSO is not configured."
