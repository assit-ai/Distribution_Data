# Distribution Daily Update Portal

Depot-wise daily entry (Orders, Delivered, Stock, Avg daily sale), auto Stock Cover, submission status,
ar boss er format e WhatsApp report. Node.js + Express + PostgreSQL.

## Role
- **Admin**: sob depot dekhe, user toiri/depot assign kore, Sales ar Issues edit kore, delivery van enlistment/bulk upload kore.
- **Depot user**: shudhu nijer assign kora depot er data dite/edit korte pare, drop-down theke shudhu nijer depot er enlisted van select korte pare.
- Eta server e enforce kora (API te check hoy), shudhu screen e lukano na.

## Fleet & Delivery Van Registration
1. **Admin Bulk Van Registration**:
   - Admin panel e **🚐 Van Registration (Bulk)** tab ache.
   - Blank Excel/CSV template download er option ache (`Blank_Van_Registration_Template.xlsx`, `Sample_Van_Registration_Template.xlsx`, `.csv`).
   - File drag-and-drop ba Excel row copy-paste kore bulk upload kora jay.
   - Column format: `Depot` | `Vehicle_Reg_No` | `Driver_Name` | `Driver_Mobile` | `Vehicle_Type` | `Status`.
2. **User End Easy Dropdown**:
   - User end theke "Van on road" manual field bad deya hoyeche.
   - Depot incharge jokhon van dispatch log korbe, drop-down theke shudhu tar depot er enlisted vehicle select korbe (ek user er van onno user dekhte pabe na).
   - Driver name auto-fill hoye jabe.
3. **Admin Dashboard "Vans On Road" & Hyperlink Drill-downs**:
   - Admin dashboard e **Vans On Road** tab/card ache, jar hisab: `Total Assigned - Delivery Completed - Under Maintenance`.
   - Sob gulo KPI card e clickable hyperlink drill-down modal ache:
     - 📈 **Today GT Frozen Sales**: Sales vs Target, achievement %, MTD, top/low depot.
     - 🚚 **Vans On Road**: Depot-wise calculation ebong active on-road vehicles list.
     - 🚚 **Dispatched Vans**: Departure time, vehicle no, driver, route log.
     - 📦 **Pending Deliveries**: Depot & category-wise pending fulfillment breakdown.
     - 🛠️ **Under Maintenance**: Workshop e thaka gari, maintenance status, repair notes.
     - 📋 **Depots Submitted**: Kon kon depot submission complete koreche ar kara pending.

## Login ar password
- Prottek user nijer **ID (Username) + password** diye login kore. Admin user toiri korar somoy ID, Name, Designation, Mobile, Role, Depot ar prothom password dey.
- Notun user prothom login korle **nijer notun password set korte hoy** (na korle kono kaj korte dey na).
- Je kono user login er por **Password change** button theke password bodlate pare.
- **Password bhule gele:** Login page e "Password bhule gechen?" te ID + registered mobile dile admin er panel e request ashe. Mobile mile gele ✅ dekhay. Admin mobile e verify kore user er "Reset password" ghore notun password dey, request nijei muche jay. User abar login kore nijer password set kore.

## Folder
```
server.js          backend (API + login + database)
public/index.html  frontend (login + portal)
render.yaml        Render Blueprint (web service + Postgres)
package.json
.env.example
```

## Local e chalate (optional)
1. Node 18+ ar PostgreSQL lagbe. `createdb daily_update`
2. `.env.example` copy kore `.env` banan, ba terminal e export korun:
   `DATABASE_URL`, `ADMIN_PASSWORD` (min 8 char), `SESSION_SECRET`
3. `npm install` then `npm start`, browser e http://localhost:3000
4. Prothombar server chalu hole `ADMIN_USERNAME` / `ADMIN_PASSWORD` diye admin toiri hoy.

## GitHub e upload
```
cd daily_update_portal
git init
git add .
git commit -m "Daily update portal"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```
(`.env` ar `node_modules` .gitignore e ache, upload hobe na. Repo **private** rakhun.)

## Render e deploy
1. render.com e login kore GitHub account connect korun.
2. **New → Blueprint** → apnar repo select korun. Render `render.yaml` poRe web service + Postgres toiri korbe.
3. Render `ADMIN_PASSWORD` jiggesh korbe. Ekta shokto password din (min 8 char). `ADMIN_USERNAME` default `admin`.
4. Apply korun. Build sesh hole `https://daily-update-portal-xxxx.onrender.com` pabeen.
5. Login: username `admin`, ar uporer password.

## Prothom setup (admin)
1. Login kore niche **Admin: Users & depot assignment** e jan.
2. Prottek depot incharge er jonno user toiri korun (Username, Name, Role = Depot user, Depot = tar depot er nam, Password).
   Ekadhik depot hole comma diye likhun: `Dhaka, Gazipur`.
3. Username/password depot incharge der janan. Tara login kore shudhu nijer depot e entry dibe.
4. Depot er nam spelling ek rakhun, ei nam gulo diyei status ar report hoy.
5. Prottodin Sales ar Issues bosiye **Save sales & issues**, tarpor **Generate report → Copy**.

## Render Free Plan e Server Awake / Keep-Alive Rakhar Poddhoti
Render er Free Tier e 15 minute kono visitor na asle web service sleep (spin down) hoye jay, tarpor abar open korte 50-70 second lage (Cold Start)। Eta bondho korar jonno 2ti poddhoti set kora hoyeche:

### 1. Automatic Self-Ping (Built-in)
Server launch hole `RENDER_EXTERNAL_URL` (ba `APP_URL`) bebohar kore proti 10 minute por por nijer `/ping` endpoint e automatic request pathay। Jar fole 15-minute inactivity timer reset hoye jay ebong server awake thake।

### 2. External Monitor Setup (UptimeRobot ba cron-job.org) - 100% Guaranteed
Internal server jodi kokhono restart hoy ba sleep hoye jay, bahir theke ping korle server 24/7 active thake।
1. **[cron-job.org](https://cron-job.org)** ba **[uptimerobot.com](https://uptimerobot.com)** e free account khulun।
2. **Add Monitor / Create Cronjob**:
   - **URL**: `https://<your-app-name>.onrender.com/ping` (ba `/healthz`)
   - **Interval / Schedule**: Every **10 minutes** (ba 5 minutes)
   - **Monitor Type**: HTTP(s)
3. Save korun। Ekhon Render free tier e thakleo kono sleep hobe na, 24 ghontai instant load hobe!

## Free plan er Database shimaboddhota (Render docs onujayi)
- **Free Postgres 30 din por expire kore** (14 din grace), tarpor data muche jay, ar free te backup nai।
- Permanent production er jonno: `DATABASE_URL` e free cloud Postgres jemon [Supabase](https://supabase.com) ba [Neon.tech](https://neon.tech) (lifetime free tier) connect korte paren।

## Nirapotta
- Password bcrypt diye hash kora, session cookie httpOnly + secure (production e).
- 8 bar vul password dile 15 minute er jonno block.
- Admin password ar `SESSION_SECRET` kokhono GitHub e rakhben na (Render dashboard e rakhun).
- Kew chole gele Admin panel theke user delete korun ba depot khali kore din.
