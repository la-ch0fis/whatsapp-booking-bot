# TESTING & DEPLOYMENT GUIDE — zero to production, spoon-fed edition

Read this top to bottom, do it in order. Each phase proves ONE new layer and
costs $0 until the very last step. When something breaks, the phase where it
breaks tells you which layer to blame — that's your debug tree.

**Big reassurance up front:** there is no "WhatsApp library" to learn. The bot
talks to Meta with plain `requests` POSTs, and Meta talks to the bot with plain
HTTP webhooks. You have consumed a thousand REST APIs as a DE. This is that.

---

# PART 1 — ONE-TIME SETUP ON YOUR MACHINE

## 1.1 Python environment (Ubuntu 24.04)

Lucky you: Ubuntu 24.04 ships Python 3.12 — the EXACT same version as the
Lambda runtime, so local == cloud, no surprises.

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip
cd wa_bot
python3 --version          # should say 3.12.x
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

(Inside the venv, plain `pip install` works. Only Ubuntu's SYSTEM pip demands
`--break-system-packages` — if you ever see that error, it means you forgot
to activate the venv. Since you normally drive envs with uv: `uv venv` +
`uv pip install -r requirements.txt` works identically here if you prefer.)

That installs: flask + gunicorn (local web server), requests (calls to Meta),
python-dateutil (the parser), the three google-* packages (Calendar API),
boto3 (DynamoDB/SSM), pytest (tests). That's the ENTIRE dependency list.

## 1.2 Google Calendar credentials

Copy `token.json` from your Telegram bot project into this folder. Same file,
same scopes, nothing to redo. If you ever lose it, re-run whatever OAuth
bootstrap script you used for the Telegram bot to regenerate it.

## 1.3 Tunnel tool (needed from Phase 2 on)

Meta must reach your laptop over public HTTPS. Pick ONE:

```bash
# Option A: cloudflared (no account needed) — .deb install on Ubuntu:
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
cloudflared --version

# Option B: ngrok (needs a free account + authtoken from ngrok.com)
```

## 1.4 AWS tooling (needed from Phase 3 on)

```bash
# AWS CLI v2 — do NOT `apt install awscli` (that's the ancient v1). Official zip:
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install
aws --version              # want aws-cli/2.x

aws configure
#   AWS Access Key ID:     <from an IAM user with admin or PowerUser access>
#   AWS Secret Access Key: <same>
#   Default region name:   us-east-1     (cheapest/simplest; mx-central-1 exists if you want Mexico)
#   Default output format: json
aws sts get-caller-identity   # sanity check — should print your account ID

# SAM CLI — official zip installer (no snap, no pip-into-system):
wget https://github.com/aws/aws-sam-cli/releases/latest/download/aws-sam-cli-linux-x86_64.zip
unzip aws-sam-cli-linux-x86_64.zip -d sam-installation
sudo ./sam-installation/install
sam --version
```

If you don't have an IAM user yet: AWS Console → IAM → Users → Create user →
attach policy `AdministratorAccess` (fine for a personal account; tighten later)
→ Security credentials tab → Create access key → "CLI" → copy both values.

---

# PART 2 — LOCAL TESTING, PHASE BY PHASE

## Phase 0 — Parser only (5 min, nothing external)

```bash
pytest tests/ -v
```

All green = Spanish dates, relative words, junk rejection, am/pm enforcement,
impossible dates, Sundays/lunch/hours/holidays all behave. If anything fails
here, nothing downstream matters yet.

## Phase 1 — Chat with the bot in your terminal (10 min, still nothing external)

`repl.py` replaces the "send to WhatsApp" functions with `print()` so you can
play customer without a phone, a tunnel, or AWS.

```bash
export VERIFY_TOKEN=x WHATSAPP_TOKEN=x PHONE_NUMBER_ID=x   # dummies; Meta is never called
python3 repl.py --fake-cal      # simulated calendar: every slot free
```

Script to run through (type each line; when buttons show, type the ID in brackets):

```
hola                      -> welcome menu
agendar                   -> asks your name
Laura                     -> asks date+time
Pringles, 5pm             -> MUST reject with the "junk" message
el 20 a las 5             -> MUST reject asking for am/pm
mañana a las 4pm          -> confirm button (or sunday/holiday error if tomorrow is one)
confirm_yes               -> booked 🎉
cancelar                  -> lists your booking
cx_fake_0                 -> asks are-you-sure
cancel_yes                -> cancelled
agendar                   -> ...now wait 90+ seconds...
/sweep                    -> should print expired: 1 + the timeout message
/quit
```

Then run it again WITHOUT `--fake-cal` to hit your REAL Google Calendar:
book something, watch the event appear in the calendar UI with your own eyes,
cancel it, watch it disappear. Also: manually create a calendar event at some
time, then ask the bot for that exact time — it must offer alternatives.

## Phase 2 — Real WhatsApp on your real phone, still $0 (30-60 min)

Meta gives every app a FREE test number. No business verification, no payment
method, nothing. You were wrong about having to pay, in the best possible way.

**2.1 Create the Meta app (browser):**
1. Go to developers.facebook.com → log in with any Facebook account.
2. My Apps → Create App → use case: "Other" → type: **Business** → name it
   whatever ("citas-bot") → Create.
3. On the app dashboard, find the **WhatsApp** product card → "Set up".
4. It asks for a Meta Business Account → create one inline if you don't have
   one (just a name, takes 30 seconds).

**2.2 Grab your three values (API Setup page):**
- **Test number**: shown at the top ("From"). This is the number customers
  (you, for now) will message.
- **Phone number ID**: the long number right under the test number. This is
  your `PHONE_NUMBER_ID` (it is NOT the phone number itself).
- **Temporary access token**: big blue-ish string, valid 24h. This is your
  `WHATSAPP_TOKEN` for today. Regenerate daily, or make the permanent one
  now (see Phase 5, step 2 — takes 5 min and stops the daily annoyance).

**2.3 Add yourself as an allowed recipient:**
- Same page, "To" dropdown → Manage phone number list → add YOUR personal
  WhatsApp number → Meta sends you a code on WhatsApp → enter it.
- Optional smoke test: hit the "Send message" button on that page. If the
  hello_world template lands on your phone, Meta-to-you plumbing works.

**2.4 Run the bot + tunnel (two terminals):**

```bash
# Terminal 1
source .venv/bin/activate
export WHATSAPP_TOKEN="<temporary token>"
export PHONE_NUMBER_ID="<phone number id>"
export VERIFY_TOKEN="whatever123"        # you invent this; remember it
export APP_SECRET="<app secret>"         # App settings -> Basic -> App secret.
# APP_SECRET turns on webhook signature verification (forged requests -> 403).
# You may omit it for quick local play — the log will warn — but NEVER in prod.
gunicorn --workers 1 --bind 0.0.0.0:8000 app:app

# Terminal 2
cloudflared tunnel --url http://localhost:8000
# copy the https://xxxxx.trycloudflare.com URL it prints
```

**2.5 Point Meta at your laptop:**
1. App dashboard → WhatsApp → **Configuration** → Webhook → Edit.
2. Callback URL: `https://xxxxx.trycloudflare.com/webhook`  (note the /webhook!)
3. Verify token: `whatever123` (exactly what you exported).
4. Click "Verify and save" — if it errors, your gunicorn terminal will show
   whether the request even arrived (tunnel problem) or arrived with a
   mismatched token (typo problem).
5. Same page, **Webhook fields** → find `messages` → Subscribe. (Skipping this
   is THE classic "bot receives nothing" mistake.)

**2.6 The moment of truth:** from your phone, WhatsApp the test number: `hola`.
Menu should come back. Run the whole Phase 1 script again, now with real
buttons you can actually tap, and the timeout arriving on your phone ~90s
after you go silent mid-flow.

**If nothing arrives, check in this order:** (1) gunicorn terminal shows a
POST hitting /webhook? No → webhook URL/tunnel/subscription problem. (2) POST
arrives but no reply on phone → token expired (24h!) or your number isn't in
the recipient list — the gunicorn log will show a 4xx from Meta with the
reason spelled out.

## Phase 3 — Swap in DynamoDB, still running locally (30 min, ~$0)

Prove the state layer alone before involving Lambda.

```bash
aws dynamodb create-table --table-name wa-bot-dev-state \
  --attribute-definitions AttributeName=pk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
aws dynamodb update-time-to-live --table-name wa-bot-dev-state \
  --time-to-live-specification "Enabled=true, AttributeName=ttl"

export TABLE_NAME=wa-bot-dev-state
python3 repl.py --fake-cal
```

Same REPL, but state now round-trips through Dynamo. Mid-conversation, in a
third terminal: `aws dynamodb scan --table-name wa-bot-dev-state` — you should
SEE your `conv#...` item, and see it vanish after confirming a booking.
Note: with TABLE_NAME set the local sweeper thread does NOT run (that's the
sweeper Lambda's job) — timeout gets tested in Phase 4. `unset TABLE_NAME`
when done. Delete the dev table whenever: `aws dynamodb delete-table
--table-name wa-bot-dev-state`.

---

# PART 3 — DEPLOY TO AWS (Phase 4)

**4.1 Put ALL THREE secrets in SSM** (encrypted parameters; they are fetched
at runtime and never appear in CloudFormation, env vars, or samconfig.toml):

```bash
# Google token: the CONTENT of token.json (Lambda has no disk for the file)
aws ssm put-parameter --name /wa-bot/google-token --type SecureString \
  --value "$(cat token.json)"

# Meta access token (temp one is fine for now; overwrite later with the permanent one)
aws ssm put-parameter --name /wa-bot/whatsapp-token --type SecureString \
  --value "PASTE_TOKEN_HERE"

# Meta App Secret — powers webhook signature verification (CRITICAL security).
# Find it: developers.facebook.com -> your app -> App settings -> Basic -> App secret -> Show
aws ssm put-parameter --name /wa-bot/app-secret --type SecureString \
  --value "PASTE_APP_SECRET_HERE"
```

To rotate any of them later: same command + `--overwrite`. Running Lambdas
pick the new value up on their next cold start (or redeploy to force it).

**4.2 Build and deploy:**

```bash
sam build            # packages code + deps into .aws-sam/
sam deploy --guided  # first time only; afterwards plain `sam deploy`
```

`--guided` will interrogate you. Answers:

```
Stack Name:                         wa-bot
AWS Region:                         us-east-1        (or your configured one)
Parameter PhoneNumberId:            <paste it — not secret>
Parameter VerifyToken:              whatever123
Parameter WhatsAppTokenSsmParam:    (press Enter — default /wa-bot/whatsapp-token)
Parameter AppSecretSsmParam:        (press Enter — default /wa-bot/app-secret)
Parameter GoogleTokenSsmParam:      (press Enter — default /wa-bot/google-token)
Confirm changes before deploy:      y
Allow SAM CLI IAM role creation:    y                (it builds the Lambda roles for you)
Disable rollback:                   n
WebhookFunction has no auth defined, is this okay:  y   (Meta can't do IAM auth; the VERIFY_TOKEN handshake is the guard)
Save arguments to configuration file: y              (creates samconfig.toml; future deploys are just `sam deploy`)
```

Wait ~2-3 min. At the end, **Outputs** shows `WebhookUrl`. Copy it.

**4.3 Repoint Meta from your laptop to the cloud:** WhatsApp → Configuration →
Webhook → Edit → replace the tunnel URL with the `WebhookUrl` value → same
verify token → Verify and save. You can now kill gunicorn and cloudflared
forever. Your laptop is out of the loop.

**4.4 Test in the cloud** — run the full Phase 2 phone script again, plus the
cloud-only checks:

```bash
sam logs -n WebhookFunction --stack-name wa-bot --tail    # live logs while you chat
```

- Timeout: start `agendar`, go silent 2 min → the notice hits your phone
  (sweeper runs every minute, so 90s–2.5min is normal).
- Reminders pipeline: `sam remote invoke RemindersFunction --stack-name wa-bot`
  → logs should show "Bookings pending reminder: 0" (or more if you manually
  create a bot-tagged event ~24h out). Actual template delivery needs Phase 5's
  approved template; to test the pipe early, temporarily set
  REMINDER_TEMPLATE_NAME to "hello_world" in config.py and redeploy.
- State: `aws dynamodb scan --table-name $(aws cloudformation describe-stacks
  --stack-name wa-bot --query "Stacks[0].Outputs[?OutputKey=='TableName'].OutputValue" --output text)`

**When something only fails in the cloud**, it's one of three things, and
`sam logs` names it: an env var (KeyError in the log), an IAM permission
(AccessDenied in the log), or a cold-start timeout (rare here). Update code →
`sam build && sam deploy` again. Nuke everything → `sam delete`.

---

# PART 4 — GO-LIVE (Phase 5, the only step with money — barely)

1. **Business verification** in Meta Business Manager (Settings → Security
   Center). Needs real business info; takes minutes to days.
2. **Permanent token**: Business Settings → Users → System Users → Add →
   (Admin) → Generate token → select your app → check permissions
   `whatsapp_business_messaging` + `whatsapp_business_management` → copy it.
   Store it: `aws ssm put-parameter --name /wa-bot/whatsapp-token
   --type SecureString --value "<new token>" --overwrite` (no redeploy needed;
   next cold start picks it up).
3. **Real number**: add the business phone number in WhatsApp → API Setup
   (it must NOT be attached to a regular WhatsApp app — dedicated number).
4. **Template**: WhatsApp Manager → Message templates → Create →
   name `recordatorio_cita`, language Spanish (MEX), category **Utility**
   (cheap tier — make sure it doesn't get classified Marketing), body e.g.:
   `Hola 🌿 Te recordamos tu sesión agendada para el {{1}}. ¡Te esperamos!`
   → submit → usually approved within hours.
5. **Display name** review happens automatically with the real number.

**Monthly bill, realistically:** Lambda free tier (1M invocations) swallows
everything including the 43k sweeper runs; DynamoDB on-demand at this volume
rounds to $0; API Gateway ~$1/million requests; customer-service WhatsApp
messages are free; reminder templates bill per-send at the Utility rate.
Total: **less than one coffee a month**, dominated by reminders.