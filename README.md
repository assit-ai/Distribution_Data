# Distribution Daily Update Portal

Depot-wise daily entry (Orders, Delivered, Stock, Avg daily sale), auto Stock Cover, submission status,
ar boss er format e WhatsApp report. Node.js + Express + PostgreSQL.

## Role
- **Admin**: sob depot dekhe, user toiri/depot assign kore, Sales ar Issues edit kore.
- **Depot user**: shudhu nijer assign kora depot er data dite/edit korte pare. Baki sob dekhte pare.
- Eta server e enforce kora (API te check hoy), shudhu screen e lukano na.

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

## Free plan er shimaboddhota (Render docs onujayi)
- Free web service 15 minute idle thakle ghumay, abar khulte ~1 minute lage.
- **Free Postgres 30 din por expire kore** (14 din grace), tarpor data muche jay, ar free te backup nai.
- Real use er jonno: web service ar Postgres paid plan e nin, ba `DATABASE_URL` onno Postgres (jemon Neon, Supabase) e dekhiye din.
- Calendar e 30 diner reminder rakhun jodi free te cholan.

## Nirapotta
- Password bcrypt diye hash kora, session cookie httpOnly + secure (production e).
- 8 bar vul password dile 15 minute er jonno block.
- Admin password ar `SESSION_SECRET` kokhono GitHub e rakhben na (Render dashboard e rakhun).
- Kew chole gele Admin panel theke user delete korun ba depot khali kore din.
