'use strict';
const express = require('express');
const { Pool } = require('pg');
const bcrypt = require('bcryptjs');
const cookieSession = require('cookie-session');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

const prod = process.env.NODE_ENV === 'production';
if (!process.env.DATABASE_URL) { console.error('DATABASE_URL is not set'); process.exit(1); }
if (prod && !process.env.SESSION_SECRET) { console.error('SESSION_SECRET is not set'); process.exit(1); }

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.PGSSL === 'false' ? false : (prod ? { rejectUnauthorized: false } : false)
});

const app = express();
app.set('trust proxy', 1);

// Lightweight health check & keep-alive ping endpoints (Zero DB load, fast HTTP 200)
app.get(['/healthz', '/ping', '/api/health'], (req, res) => {
  res.status(200).json({ status: 'ok', uptime: Math.floor(process.uptime()), timestamp: Date.now() });
});
app.head(['/healthz', '/ping', '/api/health'], (req, res) => {
  res.status(200).end();
});

app.use(express.json({ limit: '2mb' }));
app.use(cookieSession({
  name: 'dup', keys: [process.env.SESSION_SECRET || 'dev-only-secret'],
  httpOnly: true, sameSite: 'lax', secure: prod
}));

// Mutating requests must be JSON (basic CSRF protection along with sameSite cookie)
app.use((req, res, next) => {
  if (['POST', 'PUT', 'DELETE'].includes(req.method) && !req.is('application/json') && req.path.startsWith('/api/') && req.method !== 'DELETE')
    return res.status(415).json({ error: 'JSON payload required' });
  next();
});

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const wrap = fn => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
const lc = s => String(s || '').trim().toLowerCase();
const cleanDepots = a => [...new Set((Array.isArray(a) ? a : String(a || '').split(','))
  .map(x => String(x).trim()).filter(Boolean).map(x => x.slice(0, 60)))];
const normMobile = s => { const m = String(s || '').replace(/[\s-]/g, ''); return m === '' ? '' : (/^\+?[0-9]{7,15}$/.test(m) ? m : null); };
const last10 = m => String(m || '').replace(/\D/g, '').slice(-10);
const nonNeg = v => { const n = Number(v); return Number.isFinite(n) && n >= 0 && n < 1e12 ? n : null; };

// ---------- DB setup ----------
async function init() {
  await pool.query(`
    create table if not exists users(
      id serial primary key, username text unique not null, password_hash text not null,
      name text not null default '', role text not null default 'depot' check (role in ('admin','depot')),
      depots text[] not null default '{}', created_at timestamptz not null default now());
    create table if not exists categories(
      id serial primary key, name text unique not null, display_order int not null default 0,
      created_at timestamptz not null default now());
    create table if not exists entries(
      id serial primary key, entry_date date not null, depot text not null, category text not null,
      orders int not null default 0, delivered int not null default 0,
      stock numeric not null default 0, avg_daily numeric not null default 0,
      updated_by int references users(id) on delete set null, updated_at timestamptz not null default now(),
      unique(entry_date, depot, category));
    create table if not exists reports(
      report_date date primary key, head text not null default '',
      today_sales numeric not null default 0, today_target numeric not null default 0,
      mtd_sales numeric not null default 0, mtd_target numeric not null default 0,
      issues text not null default '');
    alter table users add column if not exists mobile text not null default '';
    alter table users add column if not exists designation text not null default '';
    alter table users add column if not exists must_change boolean not null default false;
    create table if not exists reset_requests(
      id serial primary key, user_id int not null unique references users(id) on delete cascade,
      mobile text not null default '', mobile_match boolean not null default false, created_at timestamptz not null default now());
    create table if not exists depot_vehicles(
      id serial primary key, entry_date date not null, depot text not null,
      total_vehicles int not null default 0, dispatched int not null default 0,
      on_road int not null default 0, delivered int not null default 0,
      notes text not null default '', updated_by int references users(id) on delete set null,
      updated_at timestamptz not null default now(), unique(entry_date, depot));
    alter table reports add column if not exists top_depot text not null default '';
    alter table reports add column if not exists low_depot text not null default '';
    alter table categories add column if not exists unit text not null default 'Qty';
    alter table users add column if not exists categories text[] not null default '{}';
    alter table depot_vehicles add column if not exists under_maintenance int not null default 0;
    create table if not exists vehicle_dispatches(
      id serial primary key, entry_date date not null, depot text not null,
      vehicle_no text not null, dispatch_time text not null default '',
      driver_name text not null default '', destination text not null default '',
      status text not null default 'dispatched', notes text not null default '',
      created_by int references users(id) on delete set null, created_at timestamptz not null default now());
    alter table entries add column if not exists remarks text not null default '';
    alter table entries add column if not exists pending_orders int not null default 0;
    alter table entries add column if not exists total_orders_count int not null default 0;
    alter table entries add column if not exists pending_so_numbers text not null default '';
    create table if not exists registered_vans(
      id serial primary key, depot text not null,
      vehicle_no text not null, driver_name text not null default '',
      driver_mobile text not null default '', vehicle_type text not null default 'Pickup Van',
      status text not null default 'active', created_at timestamptz not null default now(),
      unique(depot, vehicle_no));
    create table if not exists erp_depot_mappings(
      id serial primary key, category text not null,
      depot_code text not null, depot_name text not null default '',
      godown_name text not null default '', godown_id text not null default '',
      ref_patterns text[] not null default '{}', is_default boolean not null default false,
      created_at timestamptz not null default now(), unique(category, depot_code));
  `);

  // Ensure entries table columns support decimals (e.g. Chicken in Kg)
  try {
    await pool.query(`
      alter table entries alter column orders type numeric using orders::numeric;
      alter table entries alter column delivered type numeric using delivered::numeric;
    `);
  } catch (e) {
    // Already numeric or ignored
  }

  // Unblock any users previously forced into must_change
  await pool.query('update users set must_change = false where must_change = true');

  // Categories initialization with standard Units of Measurement (UoM)
  const defaultCats = [
    { name: 'Frozen', unit: 'Pkt' },
    { name: 'Chicken', unit: 'Kg' },
    { name: 'Egg', unit: 'Pcs' },
    { name: 'Dairy', unit: 'Ltr' },
    { name: 'Tea', unit: 'Kg' },
    { name: 'Sweets', unit: 'Kg' },
    { name: 'Others', unit: 'Pcs' }
  ];
  for (let i = 0; i < defaultCats.length; i++) {
    await pool.query(`
      insert into categories(name, display_order, unit)
      values($1, $2, $3)
      on conflict (name) do update set unit = $3
    `, [defaultCats[i].name, (i + 1) * 10, defaultCats[i].unit]);
  }
  console.log('Categories & units initialized');

  // Default ERP Depot Mappings Initialization
  const defaultMappings = [
    // Frozen
    { category: 'Frozen', depot_code: 'tejgaon02', depot_name: '02. Frozen Foods Tejgaon Depot', godown_name: '02. Frozen Foods Tejgaon Depot', godown_id: 'G206', ref_patterns: ['02.TG', '02. TEJGAON', '02.TG-FZ', '02 TG', 'TEJGAON'], is_default: false },
    { category: 'Frozen', depot_code: 'ctg02', depot_name: '02. Frozen Foods Chittagong Depot', godown_name: '02. Frozen Foods Chittagong Depot', godown_id: 'G7', ref_patterns: ['02.CTG', '02. CTG', '02 CTG', '02.CHITTAGONG', 'CHITTAGONG'], is_default: false },
    { category: 'Frozen', depot_code: 'ashulia02', depot_name: '02. Frozen Foods Factory Godown', godown_name: '02. Frozen Foods Factory Godown', godown_id: 'G5', ref_patterns: ['02. FROZEN FOOD', '02.FROZEN FOOD', '02 FACTORY', '02.FACTORY', '02.ASH', 'ASHULIA'], is_default: false },
    { category: 'Frozen', depot_code: 'mohakhali02', depot_name: '02. Frozen Foods HO Godown', godown_name: '02. Frozen Foods HO Godown', godown_id: 'G6', ref_patterns: ['02.HO', '02. HO', '02 HO', '02.MHK', 'MOHAKHALI'], is_default: false },
    { category: 'Frozen', depot_code: 'jessore02', depot_name: '02. Frozen Foods Jessore Depot', godown_name: '02. Frozen Foods Jessore Depot', godown_id: 'G256', ref_patterns: ['02 JD', '02.JD', '02.JESSORE', '02. JESSORE', 'JESSORE'], is_default: false },
    { category: 'Frozen', depot_code: 'sylhet02', depot_name: '02. Frozen Foods Sylhet Depot', godown_name: '02. Frozen Foods Sylhet Depot', godown_id: 'G167', ref_patterns: ['02.SYLHET', '02. SYLHET', '02 SYLHET', '02.SYL', 'SYLHET'], is_default: false },

    // Chicken
    { category: 'Chicken', depot_code: 'tejgaon01', depot_name: '01. Process Tejgaon Depot', godown_name: '01. Process Tejgaon Depot', godown_id: 'G205', ref_patterns: ['01.TG', '01. TEJGAON', '01 TG', '01.TEJGAON', '01TG'], is_default: false },
    { category: 'Chicken', depot_code: 'ctg01', depot_name: '01. Process Chittagong Depot', godown_name: '01. Process Chittagong Depot', godown_id: 'G4', ref_patterns: ['01.CTG', '01. CTG', '01 CTG', '01.CHITTAGONG', '01CTG'], is_default: false },
    { category: 'Chicken', depot_code: 'gazipur01', depot_name: '01. Process Factory Godown', godown_name: '01. Process Factory Godown', godown_id: 'G2', ref_patterns: ['01 FACTORY', '01.FACTORY', '01.GAZIPUR', 'GAZIPUR', 'FACTORY'], is_default: true },
    { category: 'Chicken', depot_code: 'jessore01', depot_name: '01. Process Jessore Depot', godown_name: '01. Process Jessore Depot', godown_id: 'G255', ref_patterns: ['01 JD', '01.JD', '01.JESSORE', '01 JD', '01JD'], is_default: false },
    { category: 'Chicken', depot_code: 'sylhet01', depot_name: '01. Process Sylhet Depot', godown_name: '01. Process Sylhet Depot', godown_id: 'G166', ref_patterns: ['01.SYLHET', '01. SYLHET', '01 SYLHET', '01SYL', '01.SYL'], is_default: false },

    // Egg
    { category: 'Egg', depot_code: 'tejgaon03', depot_name: '03. Branded Egg HO / Tejgaon', godown_name: '03. Branded Egg HO Godown', godown_id: 'G9', ref_patterns: ['03HO', '003.HO', '03.HO', '03. HO', '03 HO', '003HO', '03.TG', '03TG'], is_default: true }
  ];

  for (const m of defaultMappings) {
    await pool.query(`
      insert into erp_depot_mappings(category, depot_code, depot_name, godown_name, godown_id, ref_patterns, is_default)
      values($1, $2, $3, $4, $5, $6, $7)
      on conflict(category, depot_code) do update set
        depot_name = excluded.depot_name,
        godown_name = excluded.godown_name,
        godown_id = excluded.godown_id
    `, [m.category, m.depot_code, m.depot_name, m.godown_name, m.godown_id, m.ref_patterns, m.is_default]);
  }
  await syncDepotMappingsToFile();

  // First admin initialization
  const c = await pool.query('select count(*)::int n from users');
  if (c.rows[0].n === 0) {
    const u = process.env.ADMIN_USERNAME || 'admin', p = process.env.ADMIN_PASSWORD;
    if (!p || p.length < 8) { console.error('ADMIN_PASSWORD (minimum 8 characters) is required'); process.exit(1); }
    await pool.query('insert into users(username,password_hash,name,role,must_change) values($1,$2,$3,$4,false)',
      [lc(u), await bcrypt.hash(p, 10), 'Admin', 'admin']);
    console.log('First admin user created:', lc(u));
  }
}

async function syncDepotMappingsToFile() {
  try {
    const r = await pool.query('select category, depot_code, depot_name, godown_name, godown_id, ref_patterns, is_default from erp_depot_mappings order by category, depot_code');
    const custom = {};
    for (const row of r.rows) {
      if (!custom[row.category]) custom[row.category] = { depots: {} };
      custom[row.category].depots[row.depot_code] = {
        name: row.godown_name || row.depot_name,
        godown_id: row.godown_id,
        ref_patterns: row.ref_patterns || [],
        is_default: row.is_default
      };
      if (row.is_default) {
        custom[row.category].default_depot = row.depot_code;
      }
    }
    const filePath = path.join(__dirname, 'depot_mappings.json');
    fs.writeFileSync(filePath, JSON.stringify(custom, null, 2), 'utf8');
  } catch (e) {
    console.warn('[ERP Mappings] Warning syncing mappings to file:', e.message);
  }
}

async function getAllCategories() {
  const r = await pool.query('select name from categories order by display_order, id');
  return r.rows.map(x => x.name);
}

// ---------- auth ----------
const fails = new Map();
const auth = wrap(async (req, res, next) => {
  if (!req.session.uid) return res.status(401).json({ error: 'Please login to continue' });
  const r = await pool.query('select id,username,name,role,depots,categories,mobile,designation,must_change from users where id=$1', [req.session.uid]);
  if (!r.rows[0]) { req.session = null; return res.status(401).json({ error: 'Please login to continue' }); }
  req.user = r.rows[0];
  next();
});
const admin = (req, res, next) => req.user.role === 'admin' ? next() : res.status(403).json({ error: 'Admin access required' });
async function allDepots() {
  const r = await pool.query('select distinct unnest(depots) d from users order by 1');
  return r.rows.map(x => x.d);
}
async function allowedDepots(u) { return u.role === 'admin' ? allDepots() : (u.depots || []); }
async function allowedCategories(u) {
  const all = await getAllCategories();
  if (u.role === 'admin') return all;
  if (!u.categories || !u.categories.length) return all;
  const filtered = all.filter(c => u.categories.some(uc => lc(uc) === lc(c)));
  return filtered.length ? filtered : all;
}
function normDepot(s) {
  if (!s) return '';
  s = String(s).toLowerCase().trim();
  // Preserve specific depot codes ending in two digits (e.g. tejgaon01, ctg02, tejgaon03)
  if (/^[a-z]+[0-9]{2}$/.test(s)) return s;
  if (s.includes('tejgaon') || /(^|[^a-z])(tg|02\.tg|01\.tg|03\.tg)($|[^a-z0-9])/.test(s)) return 'tejgaon';
  if (s.includes('chittagong') || s.includes('ctg')) return 'ctg';
  if (s.includes('gazipur')) return 'gazipur';
  if (s.includes('ashulia') || s.includes('factory') || s.includes('ash')) return 'ashulia';
  if (s.includes('mohakhali') || s.includes('ho godown') || s.includes('mhk') || /(^|[^a-z])ho($|[^a-z0-9])/.test(s)) return 'mohakhali';
  if (s.includes('jessore') || /(^|[^a-z])jd($|[^a-z0-9])/.test(s)) return 'jessore';
  if (s.includes('sylhet') || s.includes('syl')) return 'sylhet';
  return s.replace(/[^a-z0-9]/g, '').replace(/[0-9]+$/, '');
}

function depotMatches(d1, d2) {
  if (!d1 || !d2) return false;
  const s1 = String(d1).toLowerCase().trim();
  const s2 = String(d2).toLowerCase().trim();
  if (s1 === s2) return true;
  const n1 = normDepot(s1);
  const n2 = normDepot(s2);
  if (n1 && n2 && n1 === n2) return true;
  if (s1.includes(s2) || s2.includes(s1)) return true;
  return false;
}

const findDepot = (list, d) => list.find(x => depotMatches(x, d));

app.get('/healthz', (req, res) => res.send('ok'));

app.post('/api/login', wrap(async (req, res) => {
  const username = lc(String(req.body.username || '').trim());
  const password = String(req.body.password || '').trim();
  if (!username || !password) return res.status(400).json({ error: 'Please enter both Username and Password' });

  const key = req.ip + '|' + username, f = fails.get(key);
  if (f && f.n >= 10 && Date.now() - f.t < 15 * 60 * 1000) {
    return res.status(429).json({ error: 'Too many failed login attempts. Please try again after 15 minutes.' });
  }

  const r = await pool.query('select * from users where lower(trim(username)) = lower(trim($1))', [username]);
  const ok = r.rows[0] && await bcrypt.compare(password, r.rows[0].password_hash);
  if (!ok) {
    fails.set(key, { n: (f && Date.now() - f.t < 15 * 60 * 1000 ? f.n : 0) + 1, t: Date.now() });
    return res.status(401).json({ error: 'Invalid username or password' });
  }

  fails.delete(key);
  await pool.query('update users set must_change=false where id=$1', [r.rows[0].id]);
  req.session = { uid: r.rows[0].id };
  res.json({ ok: true });
}));

app.post('/api/logout', (req, res) => { req.session = null; res.json({ ok: true }); });
app.get('/api/me', auth, (req, res) => res.json(req.user));
app.get('/api/depots', auth, wrap(async (req, res) => res.json({ depots: await allDepots() })));

// ---------- categories management (dynamic) ----------
app.get('/api/categories', auth, wrap(async (req, res) => {
  const r = await pool.query('select id, name, display_order, coalesce(unit, \'Qty\') as unit from categories order by display_order, id');
  res.json({ categories: r.rows.map(x => x.name), details: r.rows });
}));

app.post('/api/categories', auth, admin, wrap(async (req, res) => {
  const name = String(req.body.name || '').trim();
  const unit = String(req.body.unit || 'Qty').trim().slice(0, 20);
  if (!name || name.length > 50) return res.status(400).json({ error: 'Category name is required (max 50 characters)' });
  try {
    const maxOrderRes = await pool.query('select coalesce(max(display_order), 0) + 10 as next_order from categories');
    const nextOrder = maxOrderRes.rows[0].next_order;
    await pool.query('insert into categories(name, display_order, unit) values($1, $2, $3)', [name, nextOrder, unit]);
    res.json({ ok: true, name, unit });
  } catch (e) {
    if (e.code === '23505') return res.status(409).json({ error: 'Category name already exists' });
    throw e;
  }
}));

app.delete('/api/categories/:name', auth, admin, wrap(async (req, res) => {
  const name = String(req.params.name || '').trim();
  if (!name) return res.status(400).json({ error: 'Category name required' });
  const countRes = await pool.query('select count(*)::int n from entries where lower(category)=lower($1)', [name]);
  if (countRes.rows[0].n > 0) {
    return res.status(400).json({ error: `Cannot delete: ${countRes.rows[0].n} entries exist for this category.` });
  }
  await pool.query('delete from categories where lower(name)=lower($1)', [name]);
  res.json({ ok: true });
}));

// ---------- entries ----------
app.get('/api/entries', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  const from = String(req.query.from || d);
  const to = String(req.query.to || d);
  if (!DATE.test(from) || !DATE.test(to)) return res.status(400).json({ error: 'Invalid date format' });
  
  const isRange = from !== to;
  let query, params;

  if (!isRange) {
    query = `select to_char(e.entry_date,'YYYY-MM-DD') date, e.depot, e.category, e.orders::float8 orders, e.delivered::float8 delivered,
      e.stock::float8 stock, e.avg_daily::float8 avg_daily,
      coalesce(e.pending_orders, 0)::int pending_orders,
      coalesce(e.total_orders_count, 0)::int total_orders_count,
      coalesce(e.pending_so_numbers, '') pending_so_numbers,
      coalesce(e.remarks, '') remarks, e.updated_at, u.name "by"
      from entries e left join users u on u.id=e.updated_by where e.entry_date=$1`;
    params = [from];
    if (req.user.role !== 'admin') {
      const allowedD = (await allowedDepots(req.user)).map(lc);
      const allowedC = (await allowedCategories(req.user)).map(lc);
      query += ` and lower(e.depot) = any($2) and lower(e.category) = any($3)`;
      params.push(allowedD, allowedC);
    }
    query += ` order by e.category, e.depot`;
  } else {
    // For date range: Aggregate orders, delivered, and take the latest available stock in range
    query = `
      with latest_stock as (
        select distinct on (depot, category) depot, category, stock, avg_daily, entry_date
        from entries
        where entry_date between $1 and $2
        order by depot, category, entry_date desc
      )
      select
        $2 as date,
        e.depot,
        e.category,
        sum(e.orders)::float8 as orders,
        sum(e.delivered)::float8 as delivered,
        coalesce(ls.stock, 0)::float8 as stock,
        coalesce(ls.avg_daily, 0)::float8 as avg_daily,
        sum(coalesce(e.pending_orders, 0))::int as pending_orders,
        sum(coalesce(e.total_orders_count, 0))::int as total_orders_count,
        coalesce(string_agg(distinct e.pending_so_numbers, ', ') filter (where e.pending_so_numbers <> ''), '') as pending_so_numbers,
        coalesce(string_agg(distinct e.remarks, '; ') filter (where e.remarks <> ''), '') as remarks,
        max(e.updated_at) as updated_at,
        'Range Summary' as "by"
      from entries e
      left join latest_stock ls on ls.depot = e.depot and ls.category = e.category
      where e.entry_date between $1 and $2
    `;
    params = [from, to];
    if (req.user.role !== 'admin') {
      const allowedD = (await allowedDepots(req.user)).map(lc);
      const allowedC = (await allowedCategories(req.user)).map(lc);
      query += ` and lower(e.depot) = any($3) and lower(e.category) = any($4)`;
      params.push(allowedD, allowedC);
    }
    query += ` group by e.depot, e.category, ls.stock, ls.avg_daily order by e.category, e.depot`;
  }
  
  const r = await pool.query(query, params);
  res.json({ entries: r.rows, isRange, from, to });
}));

app.put('/api/entries', auth, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Invalid date format' });
  
  const depot = findDepot(await allowedDepots(req.user), b.depot);
  if (!depot) return res.status(403).json({ error: 'You are not authorized for this depot' });

  const allowedCats = await allowedCategories(req.user);
  const cat = allowedCats.find(c => lc(c) === lc(b.category));
  if (!cat) return res.status(403).json({ error: `You are not authorized for product category: ${b.category}` });

  const orders = nonNeg(b.orders), delivered = nonNeg(b.delivered), stock = nonNeg(b.stock), avg = nonNeg(b.avg_daily);
  if ([orders, delivered, stock, avg].includes(null)) return res.status(400).json({ error: 'Invalid numerical values' });
  
  const finalOrders = cat === 'Chicken' ? orders : Math.round(orders);
  const finalDelivered = cat === 'Chicken' ? delivered : Math.round(delivered);
  const remarks = String(b.remarks || '').trim();
  const pendingOrders = b.pending_orders != null ? parseInt(b.pending_orders, 10) : (finalOrders > finalDelivered ? 1 : 0);
  const totalOrdersCount = b.total_orders_count != null ? parseInt(b.total_orders_count, 10) : (finalOrders > 0 ? 1 : 0);
  const pendingSoNumbers = String(b.pending_so_numbers || '').trim();

  await pool.query(`insert into entries(entry_date,depot,category,orders,delivered,stock,avg_daily,pending_orders,total_orders_count,pending_so_numbers,remarks,updated_by,updated_at)
    values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,now())
    on conflict(entry_date,depot,category) do update set orders=$4,delivered=$5,stock=$6,avg_daily=$7,pending_orders=$8,total_orders_count=$9,pending_so_numbers=$10,remarks=$11,updated_by=$12,updated_at=now()`,
    [date, depot, cat, finalOrders, finalDelivered, stock, avg, pendingOrders, totalOrdersCount, pendingSoNumbers, remarks, req.user.id]);
  res.json({ ok: true });
}));

app.delete('/api/entries', auth, wrap(async (req, res) => {
  const { date, depot, category } = req.query;
  if (!DATE.test(String(date || ''))) return res.status(400).json({ error: 'Invalid date format' });
  const d = findDepot(await allowedDepots(req.user), depot);
  if (!d) return res.status(403).json({ error: 'Unauthorized depot' });
  const allowedCats = await allowedCategories(req.user);
  if (!allowedCats.some(c => lc(c) === lc(category))) return res.status(403).json({ error: 'Unauthorized category' });
  await pool.query('delete from entries where entry_date=$1 and depot=$2 and category=$3', [date, d, category]);
  res.json({ ok: true });
}));

// ---------- bulk entries (Stock from Poloxy / Orders / Combined) ----------
app.post('/api/bulk/entries', auth, wrap(async (req, res) => {
  const { date, items } = req.body;
  if (!DATE.test(String(date || ''))) return res.status(400).json({ error: 'Invalid date format' });
  if (!Array.isArray(items) || !items.length) return res.status(400).json({ error: 'Data list is empty' });

  const allowed = await allowedDepots(req.user);
  const allCats = await getAllCategories();
  let updated = 0, skipped = 0;
  const skippedDetails = [];

  for (const it of items) {
    const rawDepot = String(it.depot || '').trim();
    const depot = findDepot(allowed, rawDepot) || (req.user.role === 'admin' ? rawDepot : null);
    if (!depot) {
      skipped++;
      skippedDetails.push(`Unrecognized depot: "${rawDepot}"`);
      continue;
    }

    const rawCat = String(it.category || '').trim();
    const cat = allCats.find(c => lc(c) === lc(rawCat)) || (req.user.role === 'admin' ? (rawCat || 'Frozen') : null);
    if (!cat) {
      skipped++;
      skippedDetails.push(`Unrecognized category: "${rawCat}" for depot ${depot}`);
      continue;
    }

    const o = it.orders != null && it.orders !== '' ? nonNeg(it.orders) : null;
    const v = it.delivered != null && it.delivered !== '' ? nonNeg(it.delivered) : null;
    const s = it.stock != null && it.stock !== '' ? nonNeg(it.stock) : null;
    const a = it.avg_daily != null && it.avg_daily !== '' ? nonNeg(it.avg_daily) : null;

    const finalOrders = o != null ? (cat === 'Chicken' ? o : Math.round(o)) : null;
    const finalDelivered = v != null ? (cat === 'Chicken' ? v : Math.round(v)) : null;

    await pool.query(`
      insert into entries(entry_date, depot, category, orders, delivered, stock, avg_daily, updated_by, updated_at)
      values($1, $2, $3, coalesce($4, 0), coalesce($5, 0), coalesce($6, 0), coalesce($7, 0), $8, now())
      on conflict(entry_date, depot, category) do update set
        orders = coalesce($4, entries.orders),
        delivered = coalesce($5, entries.delivered),
        stock = coalesce($6, entries.stock),
        avg_daily = coalesce($7, entries.avg_daily),
        updated_by = $8,
        updated_at = now()
    `, [date, depot, cat, finalOrders, finalDelivered, s, a, req.user.id]);
    updated++;
  }
  res.json({ ok: true, updated, skipped, skippedDetails, date });
}));

// ---------- vehicles tracking ----------
app.get('/api/vehicles', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  const from = String(req.query.from || d);
  const to = String(req.query.to || d);
  if (!DATE.test(from) || !DATE.test(to)) return res.status(400).json({ error: 'Invalid date format' });

  let query, params;
  if (from === to) {
    query = `
      select to_char(v.entry_date,'YYYY-MM-DD') date, v.depot, v.total_vehicles, v.dispatched,
        v.on_road, v.delivered, coalesce(v.under_maintenance, 0) as under_maintenance, v.notes, v.updated_at, u.name "by"
      from depot_vehicles v left join users u on u.id=v.updated_by
      where v.entry_date=$1 order by v.depot
    `;
    params = [from];
  } else {
    query = `
      with latest_v as (
        select distinct on (depot) depot, total_vehicles, dispatched, on_road, delivered, under_maintenance, notes, updated_at, updated_by
        from depot_vehicles
        where entry_date between $1 and $2
        order by depot, entry_date desc
      )
      select
        $2 as date, lv.depot, lv.total_vehicles, lv.dispatched, lv.on_road, lv.delivered,
        coalesce(lv.under_maintenance, 0) as under_maintenance, lv.notes, lv.updated_at, u.name "by"
      from latest_v lv left join users u on u.id=lv.updated_by
      order by lv.depot
    `;
    params = [from, to];
  }
  const r = await pool.query(query, params);
  res.json({ vehicles: r.rows });
}));

app.put('/api/vehicles', auth, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Invalid date format' });
  const depot = findDepot(await allowedDepots(req.user), b.depot);
  if (!depot) return res.status(403).json({ error: 'You are not authorized for this depot' });

  const total = nonNeg(b.total_vehicles) || 0;
  const dispatched = nonNeg(b.dispatched) || 0;
  const delivered = nonNeg(b.delivered) || 0;
  const maint = nonNeg(b.under_maintenance) || 0;
  const on_road = b.on_road != null && b.on_road !== '' ? (nonNeg(b.on_road) || 0) : Math.max(0, Math.round(total) - Math.round(delivered) - Math.round(maint));
  const notes = String(b.notes || '').slice(0, 500);

  await pool.query(`
    insert into depot_vehicles(entry_date, depot, total_vehicles, dispatched, on_road, delivered, under_maintenance, notes, updated_by, updated_at)
    values($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
    on conflict(entry_date, depot) do update set
      total_vehicles=$3, dispatched=$4, on_road=$5, delivered=$6, under_maintenance=$7, notes=$8, updated_by=$9, updated_at=now()
  `, [date, depot, Math.round(total), Math.round(dispatched), Math.round(on_road), Math.round(delivered), Math.round(maint), notes, req.user.id]);
  res.json({ ok: true });
}));

// ---------- individual vehicle dispatch logs ----------
app.get('/api/dispatches', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  const from = String(req.query.from || d);
  const to = String(req.query.to || d);
  if (!DATE.test(from) || !DATE.test(to)) return res.status(400).json({ error: 'Invalid date format' });

  let query = `select vd.id, to_char(vd.entry_date,'YYYY-MM-DD') date, vd.depot, vd.vehicle_no, vd.dispatch_time,
    vd.driver_name, vd.destination, vd.status, vd.notes, vd.created_at, u.name "by"
    from vehicle_dispatches vd left join users u on u.id=vd.created_by
    where vd.entry_date between $1 and $2 order by vd.entry_date, vd.depot, vd.dispatch_time, vd.id`;
  const r = await pool.query(query, [from, to]);
  let rows = r.rows;
  if (req.user.role !== 'admin') {
    const allowedD = await allowedDepots(req.user);
    rows = rows.filter(item => allowedD.some(ad => depotMatches(item.depot, ad)));
  }
  res.json({ dispatches: rows });
}));

app.post('/api/dispatches', auth, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Invalid date format' });
  const depot = findDepot(await allowedDepots(req.user), b.depot);
  if (!depot) return res.status(403).json({ error: 'You are not authorized for this depot' });
  const vehicle_no = String(b.vehicle_no || '').trim().slice(0, 50);
  if (!vehicle_no) return res.status(400).json({ error: 'Vehicle registration number is required' });
  const dispatch_time = String(b.dispatch_time || '').trim().slice(0, 30);
  const driver_name = String(b.driver_name || '').trim().slice(0, 100);
  const destination = String(b.destination || '').trim().slice(0, 150);
  const status = String(b.status || 'dispatched').trim().slice(0, 30);
  const notes = String(b.notes || '').trim().slice(0, 300);

  const r = await pool.query(`
    insert into vehicle_dispatches(entry_date, depot, vehicle_no, dispatch_time, driver_name, destination, status, notes, created_by)
    values($1, $2, $3, $4, $5, $6, $7, $8, $9) returning id
  `, [date, depot, vehicle_no, dispatch_time, driver_name, destination, status, notes, req.user.id]);

  // Sync count in depot_vehicles
  const countRes = await pool.query('select count(*)::int n from vehicle_dispatches where entry_date=$1 and lower(depot)=lower($2)', [date, depot]);
  await pool.query(`
    insert into depot_vehicles(entry_date, depot, dispatched, updated_by, updated_at)
    values($1, $2, $3, $4, now())
    on conflict(entry_date, depot) do update set dispatched = greatest(depot_vehicles.dispatched, $3), updated_at = now()
  `, [date, depot, countRes.rows[0].n, req.user.id]);

  res.json({ ok: true, id: r.rows[0].id });
}));

app.delete('/api/dispatches/:id', auth, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (!id) return res.status(400).json({ error: 'Invalid dispatch ID' });
  const dRes = await pool.query('select * from vehicle_dispatches where id=$1', [id]);
  if (!dRes.rows[0]) return res.status(404).json({ error: 'Dispatch record not found' });
  const depot = findDepot(await allowedDepots(req.user), dRes.rows[0].depot);
  if (!depot) return res.status(403).json({ error: 'Unauthorized' });
  await pool.query('delete from vehicle_dispatches where id=$1', [id]);
  res.json({ ok: true });
}));

// ---------- registered vans (fleet enlistment by depot) ----------
app.get('/api/registered-vans', auth, wrap(async (req, res) => {
  let query = `select id, depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status, created_at from registered_vans order by depot, vehicle_no`;
  const r = await pool.query(query);
  let rows = r.rows;
  if (req.user.role !== 'admin') {
    const allowedD = await allowedDepots(req.user);
    rows = rows.filter(v => allowedD.some(ad => depotMatches(v.depot, ad)));
  } else if (req.query.depot) {
    const qd = String(req.query.depot).trim();
    rows = rows.filter(v => depotMatches(v.depot, qd));
  }
  res.json({ vans: rows });
}));

// ---------- Poloxy ERP Auto Sync (Web Trigger) ----------
app.post('/api/auto-sync', auth, admin, wrap(async (req, res) => {
  const { date, from_date, to_date, category } = req.body;
  const startDate = String(from_date || date || '').trim() || new Date().toISOString().slice(0, 10);
  const endDate = String(to_date || date || startDate).trim();
  if (!DATE.test(startDate) || !DATE.test(endDate)) {
    return res.status(400).json({ error: 'Invalid date format (expected YYYY-MM-DD)' });
  }

  // Convert YYYY-MM-DD to DD/MM/YYYY for Poloxy
  const [sy, sm, sd] = startDate.split('-');
  const poloxyStart = `${sd}/${sm}/${sy}`;
  const [ey, em, ed] = endDate.split('-');
  const poloxyEnd = `${ed}/${em}/${ey}`;
  const catParam = String(category || 'all').trim();

  // Ensure latest DB mappings are written to depot_mappings.json for Python sync
  await syncDepotMappingsToFile();

  const scriptPath = path.join(__dirname, 'sync_frozen_daily.py');
  if (!fs.existsSync(scriptPath)) {
    return res.status(500).json({ error: 'sync_frozen_daily.py script not found on server' });
  }

  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  let stdoutData = '', stderrData = '';
  
  const args = [scriptPath, `--start=${poloxyStart}`, `--end=${poloxyEnd}`, `--category=${catParam}`, '--json'];
  const child = spawn(pythonCmd, args, {
    cwd: __dirname,
    env: { ...process.env, PYTHONUNBUFFERED: '1' }
  });

  child.stdout.on('data', chunk => { stdoutData += chunk.toString(); });
  child.stderr.on('data', chunk => { stderrData += chunk.toString(); });

  let replied = false;
  child.on('error', err => {
    if (replied) return;
    replied = true;
    return res.status(500).json({
      error: `Could not start Python: ${err.message}. If running in cloud container without direct ERP access, run 'Run_Frozen_Daily_Sync.bat' locally on your office network.`,
      details: err.message
    });
  });

  child.on('close', async code => {
    if (replied) return;
    replied = true;

    if (code !== 0) {
      return res.status(500).json({
        error: `Poloxy ERP sync exited with code ${code}. Check logs below.`,
        stdout: stdoutData,
        stderr: stderrData
      });
    }

    const jsonMatch = stdoutData.match(/__JSON_START__(.*?)__JSON_END__/s);
    if (!jsonMatch) {
      return res.status(500).json({
        error: 'Sync finished but no structured JSON was returned',
        stdout: stdoutData
      });
    }

    try {
      const items = JSON.parse(jsonMatch[1]);
      const allowed = await allDepots();
      const allCats = await getAllCategories();
      let saved = 0;

      for (const it of items) {
        const depot = findDepot(allowed, it.depot) || it.depot;
        const cat = allCats.find(c => lc(c) === lc(it.category)) || it.category || 'Frozen';
        const ordVal = nonNeg(it.orders) || 0;
        const delVal = nonNeg(it.delivered) || 0;
        const stkVal = nonNeg(it.stock) || 0;
        const avgVal = nonNeg(it.avg_daily != null ? it.avg_daily : it.avg_daily_mtd) || 0;
        const itDate = it.entry_date || endDate;

        const finalOrders = cat === 'Chicken' ? ordVal : Math.round(ordVal);
        const finalDelivered = cat === 'Chicken' ? delVal : Math.round(delVal);

        const remarksVal = String(it.remarks || '').trim();
        const pendingOrders = parseInt(it.pending_orders, 10) || 0;
        const totalOrdersCount = parseInt(it.total_orders_count, 10) || 0;
        const pendingSoNumbers = String(it.pending_so_numbers || '').trim();

        await pool.query(`
          insert into entries(entry_date, depot, category, orders, delivered, stock, avg_daily, pending_orders, total_orders_count, pending_so_numbers, remarks, updated_by, updated_at)
          values($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, now())
          on conflict(entry_date, depot, category) do update set
            orders = excluded.orders,
            delivered = excluded.delivered,
            stock = excluded.stock,
            avg_daily = excluded.avg_daily,
            pending_orders = excluded.pending_orders,
            total_orders_count = excluded.total_orders_count,
            pending_so_numbers = excluded.pending_so_numbers,
            remarks = excluded.remarks,
            updated_by = excluded.updated_by,
            updated_at = now()
        `, [itDate, depot, cat, finalOrders, finalDelivered, stkVal, avgVal, pendingOrders, totalOrdersCount, pendingSoNumbers, remarksVal, req.user.id]);
        saved++;
      }

      res.json({
        ok: true,
        date: endDate,
        startDate,
        endDate,
        category: catParam,
        saved,
        items,
        stdout: stdoutData
      });
    } catch (e) {
      res.status(500).json({
        error: 'Failed to process sync results into database: ' + e.message,
        stdout: stdoutData
      });
    }
  });
}));

// ---------- ERP depot mappings (Admin configurable) ----------
app.get('/api/erp-depots', auth, admin, wrap(async (req, res) => {
  const r = await pool.query('select * from erp_depot_mappings order by category, depot_code');
  res.json({ mappings: r.rows });
}));

app.post('/api/erp-depots', auth, admin, wrap(async (req, res) => {
  const b = req.body;
  const category = String(b.category || '').trim();
  const depot_code = String(b.depot_code || '').trim().toLowerCase();
  const depot_name = String(b.depot_name || '').trim();
  const godown_name = String(b.godown_name || '').trim();
  const godown_id = String(b.godown_id || '').trim();
  const is_default = Boolean(b.is_default);
  const ref_patterns = Array.isArray(b.ref_patterns)
    ? b.ref_patterns.map(x => String(x).trim()).filter(Boolean)
    : String(b.ref_patterns || '').split(',').map(x => x.trim()).filter(Boolean);

  if (!category) return res.status(400).json({ error: 'Category is required' });
  if (!depot_code) return res.status(400).json({ error: 'Depot code is required (e.g. tejgaon01)' });

  await pool.query(`
    insert into erp_depot_mappings(category, depot_code, depot_name, godown_name, godown_id, ref_patterns, is_default)
    values($1, $2, $3, $4, $5, $6, $7)
    on conflict(category, depot_code) do update set
      depot_name = excluded.depot_name,
      godown_name = excluded.godown_name,
      godown_id = excluded.godown_id,
      ref_patterns = excluded.ref_patterns,
      is_default = excluded.is_default
  `, [category, depot_code, depot_name || depot_code, godown_name, godown_id, ref_patterns, is_default]);

  await syncDepotMappingsToFile();
  res.json({ ok: true });
}));

app.delete('/api/erp-depots/:id', auth, admin, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (!id) return res.status(400).json({ error: 'Invalid ID' });
  await pool.query('delete from erp_depot_mappings where id=$1', [id]);
  await syncDepotMappingsToFile();
  res.json({ ok: true });
}));

app.post('/api/registered-vans', auth, admin, wrap(async (req, res) => {
  const b = req.body;
  const depot = String(b.depot || '').trim();
  const vehicle_no = String(b.vehicle_no || '').trim().toUpperCase();
  if (!depot) return res.status(400).json({ error: 'Depot is required' });
  if (!vehicle_no) return res.status(400).json({ error: 'Vehicle registration number is required' });
  const driver_name = String(b.driver_name || '').trim().slice(0, 100);
  const driver_mobile = String(b.driver_mobile || '').trim().slice(0, 30);
  const vehicle_type = String(b.vehicle_type || 'Pickup Van').trim().slice(0, 50);
  const status = String(b.status || 'active').trim().slice(0, 30);

  const r = await pool.query(`
    insert into registered_vans(depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status)
    values($1, $2, $3, $4, $5, $6)
    on conflict(depot, vehicle_no) do update set
      driver_name = excluded.driver_name,
      driver_mobile = excluded.driver_mobile,
      vehicle_type = excluded.vehicle_type,
      status = excluded.status
    returning id
  `, [depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status]);
  res.json({ ok: true, id: r.rows[0].id });
}));

app.delete('/api/registered-vans/:id', auth, admin, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (!id) return res.status(400).json({ error: 'Invalid van ID' });
  await pool.query('delete from registered_vans where id=$1', [id]);
  res.json({ ok: true });
}));

app.post('/api/bulk/registered-vans', auth, admin, wrap(async (req, res) => {
  const { items } = req.body;
  if (!Array.isArray(items) || !items.length) return res.status(400).json({ error: 'No van records provided' });
  let saved = 0, skipped = 0;
  for (const it of items) {
    const depot = String(it.depot || '').trim();
    const vehicle_no = String(it.vehicle_no || it.reg_no || it.vehicle_reg_no || '').trim().toUpperCase();
    if (!depot || !vehicle_no) { skipped++; continue; }
    const driver_name = String(it.driver_name || it.driver || '').trim().slice(0, 100);
    const driver_mobile = String(it.driver_mobile || it.mobile || '').trim().slice(0, 30);
    const vehicle_type = String(it.vehicle_type || it.type || 'Pickup Van').trim().slice(0, 50);
    const status = String(it.status || 'active').trim().slice(0, 30);

    await pool.query(`
      insert into registered_vans(depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status)
      values($1, $2, $3, $4, $5, $6)
      on conflict(depot, vehicle_no) do update set
        driver_name = excluded.driver_name,
        driver_mobile = excluded.driver_mobile,
        vehicle_type = excluded.vehicle_type,
        status = excluded.status
    `, [depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status]);
    saved++;
  }
  res.json({ ok: true, saved, skipped });
}));

app.post('/api/bulk/vehicles', auth, wrap(async (req, res) => {
  const { date, items } = req.body;
  if (!DATE.test(String(date || ''))) return res.status(400).json({ error: 'Invalid date format' });
  if (!Array.isArray(items) || !items.length) return res.status(400).json({ error: 'Data list is empty' });
  const allowed = await allowedDepots(req.user);
  let updated = 0, skipped = 0;
  for (const it of items) {
    const depot = findDepot(allowed, it.depot);
    if (!depot) { skipped++; continue; }
    const total = Math.round(nonNeg(it.total_vehicles) || 0);
    const dispatched = Math.round(nonNeg(it.dispatched) || 0);
    const delivered = Math.round(nonNeg(it.delivered) || 0);
    const maint = Math.round(nonNeg(it.under_maintenance) || 0);
    const on_road = it.on_road != null && it.on_road !== '' ? Math.round(nonNeg(it.on_road) || 0) : Math.max(0, total - delivered - maint);
    const notes = String(it.notes || '').slice(0, 500);
    await pool.query(`
      insert into depot_vehicles(entry_date, depot, total_vehicles, dispatched, on_road, delivered, under_maintenance, notes, updated_by, updated_at)
      values($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
      on conflict(entry_date, depot) do update set
        total_vehicles=$3, dispatched=$4, on_road=$5, delivered=$6, under_maintenance=$7, notes=$8, updated_by=$9, updated_at=now()
    `, [date, depot, total, dispatched, on_road, delivered, maint, notes, req.user.id]);
    updated++;
  }
  res.json({ ok: true, updated, skipped });
}));

// ---------- sales & issues report (admin write) ----------
app.get('/api/report', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  const from = String(req.query.from || d);
  const to = String(req.query.to || d);
  if (!DATE.test(from) || !DATE.test(to)) return res.status(400).json({ error: 'Invalid date format' });

  if (from === to) {
    const r = await pool.query(`select head, today_sales::float8, today_target::float8, mtd_sales::float8, mtd_target::float8, top_depot, low_depot, issues
      from reports where report_date=$1`, [from]);
    return res.json({ report: r.rows[0] || { head: 'Atikur', today_sales: 0, today_target: 0, mtd_sales: 0, mtd_target: 0, top_depot: '', low_depot: '', issues: '' } });
  }

  // Date range aggregation for overview
  const r = await pool.query(`
    select
      max(head) as head,
      coalesce(sum(today_sales), 0)::float8 as today_sales,
      coalesce(sum(today_target), 0)::float8 as today_target,
      coalesce(max(mtd_sales), 0)::float8 as mtd_sales,
      coalesce(max(mtd_target), 0)::float8 as mtd_target,
      max(top_depot) as top_depot,
      max(low_depot) as low_depot,
      string_agg(distinct issues, ' | ') as issues
    from reports
    where report_date between $1 and $2
  `, [from, to]);
  res.json({ report: r.rows[0] || { head: 'Atikur', today_sales: 0, today_target: 0, mtd_sales: 0, mtd_target: 0, top_depot: '', low_depot: '', issues: '' } });
}));

app.put('/api/report', auth, admin, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Invalid date format' });
  const n = [b.today_sales, b.today_target, b.mtd_sales, b.mtd_target].map(v => nonNeg(v || 0));
  if (n.includes(null)) return res.status(400).json({ error: 'Invalid numerical values' });
  await pool.query(`insert into reports(report_date,head,today_sales,today_target,mtd_sales,mtd_target,top_depot,low_depot,issues) values($1,$2,$3,$4,$5,$6,$7,$8,$9)
    on conflict(report_date) do update set head=$2,today_sales=$3,today_target=$4,mtd_sales=$5,mtd_target=$6,top_depot=$7,low_depot=$8,issues=$9`,
    [date, String(b.head || 'Atikur').slice(0, 100), ...n, String(b.top_depot || '').slice(0, 100), String(b.low_depot || '').slice(0, 100), String(b.issues || '').slice(0, 5000)]);
  res.json({ ok: true });
}));

// ---------- Purge / Clear Data (Admin Only) ----------
app.post('/api/admin/clear-data', auth, admin, wrap(async (req, res) => {
  const { from_date, to_date, clear_entries, clear_dispatches, clear_vehicles, clear_reports, depot, category } = req.body;
  if (!DATE.test(String(from_date || '')) || !DATE.test(String(to_date || ''))) {
    return res.status(400).json({ error: 'Valid From and To dates required (format: YYYY-MM-DD)' });
  }

  let deleted_entries = 0, deleted_dispatches = 0, deleted_vehicles = 0, deleted_reports = 0;

  if (clear_entries) {
    let q = 'delete from entries where entry_date between $1 and $2';
    const params = [from_date, to_date];
    if (depot && depot !== 'all') {
      params.push(depot);
      q += ` and lower(depot) = lower($${params.length})`;
    }
    if (category && category !== 'all') {
      params.push(category);
      q += ` and lower(category) = lower($${params.length})`;
    }
    const r = await pool.query(q, params);
    deleted_entries = r.rowCount || 0;
  }

  if (clear_dispatches) {
    let q = 'delete from vehicle_dispatches where entry_date between $1 and $2';
    const params = [from_date, to_date];
    if (depot && depot !== 'all') {
      params.push(depot);
      q += ` and lower(depot) = lower($${params.length})`;
    }
    const r = await pool.query(q, params);
    deleted_dispatches = r.rowCount || 0;
  }

  if (clear_vehicles) {
    let q = 'delete from depot_vehicles where entry_date between $1 and $2';
    const params = [from_date, to_date];
    if (depot && depot !== 'all') {
      params.push(depot);
      q += ` and lower(depot) = lower($${params.length})`;
    }
    const r = await pool.query(q, params);
    deleted_vehicles = r.rowCount || 0;
  }

  if (clear_reports) {
    const r = await pool.query('delete from reports where report_date between $1 and $2', [from_date, to_date]);
    deleted_reports = r.rowCount || 0;
  }

  res.json({
    ok: true,
    deleted_entries,
    deleted_dispatches,
    deleted_vehicles,
    deleted_reports,
    total_deleted: deleted_entries + deleted_dispatches + deleted_vehicles + deleted_reports
  });
}));

// ---------- user management (admin) ----------
const USERNAME = /^[a-z0-9_.-]{3,30}$/;
app.get('/api/users', auth, admin, wrap(async (req, res) => {
  const r = await pool.query('select id,username,name,role,depots,categories,mobile,designation from users order by role, name, username');
  res.json({ users: r.rows });
}));

app.post('/api/users', auth, admin, wrap(async (req, res) => {
  const b = req.body;
  const username = lc(String(b.username || '').trim().replace(/\s+/g, '_'));
  const password = String(b.password || '').trim();

  if (!USERNAME.test(username)) return res.status(400).json({ error: 'User ID must be 3-30 characters (letters, numbers, underscores, dots)' });
  if (password.length < 8) return res.status(400).json({ error: 'Password must be at least 8 characters long' });
  const mobile = normMobile(b.mobile);
  if (mobile === null) return res.status(400).json({ error: 'Invalid mobile number format' });
  const role = b.role === 'admin' ? 'admin' : 'depot';

  try {
    await pool.query('insert into users(username,password_hash,name,role,depots,categories,mobile,designation,must_change) values($1,$2,$3,$4,$5,$6,$7,$8,false)',
      [username, await bcrypt.hash(password, 10), String(b.name || '').slice(0, 80), role, cleanDepots(b.depots), cleanDepots(b.categories), mobile, String(b.designation || '').slice(0, 80)]);
    fails.clear();
  } catch (e) {
    if (e.code === '23505') return res.status(409).json({ error: 'User ID already exists. Please choose a different ID.' });
    throw e;
  }
  res.json({ ok: true });
}));

app.put('/api/users/:id', auth, admin, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10), b = req.body;
  if (!id) return res.status(400).json({ error: 'Invalid user ID' });
  const role = b.role === 'admin' ? 'admin' : 'depot';
  if (id === req.user.id && role !== 'admin') return res.status(400).json({ error: 'Cannot revoke your own admin role' });
  const mobile = normMobile(b.mobile);
  if (mobile === null) return res.status(400).json({ error: 'Invalid mobile number format' });
  const pw = String(b.password || '').trim();
  if (pw && pw.length < 8) return res.status(400).json({ error: 'Password must be at least 8 characters long' });

  await pool.query('update users set name=$1, role=$2, depots=$3, categories=$4, mobile=$5, designation=$6 where id=$7',
    [String(b.name || '').slice(0, 80), role, cleanDepots(b.depots), cleanDepots(b.categories), mobile, String(b.designation || '').slice(0, 80), id]);
  
  if (pw) {
    await pool.query('update users set password_hash=$1, must_change=false where id=$2', [await bcrypt.hash(pw, 10), id]);
    await pool.query('delete from reset_requests where user_id=$1', [id]);
    fails.clear();
  }
  res.json({ ok: true });
}));

app.delete('/api/users/:id', auth, admin, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (id === req.user.id) return res.status(400).json({ error: 'Cannot delete your own account' });
  await pool.query('delete from users where id=$1', [id]);
  res.json({ ok: true });
}));

// ---------- password: change, forgot, reset requests ----------
app.post('/api/password', auth, wrap(async (req, res) => {
  const cur = String(req.body.current || '').trim(), nw = String(req.body.password || '').trim();
  if (nw.length < 8) return res.status(400).json({ error: 'New password must be at least 8 characters long' });
  if (nw === cur) return res.status(400).json({ error: 'New password must be different from current password' });
  const r = await pool.query('select password_hash from users where id=$1', [req.user.id]);
  if (!(await bcrypt.compare(cur, r.rows[0].password_hash))) return res.status(400).json({ error: 'Incorrect current password' });
  await pool.query('update users set password_hash=$1, must_change=false where id=$2', [await bcrypt.hash(nw, 10), req.user.id]);
  res.json({ ok: true });
}));

const fAttempts = new Map();
app.post('/api/forgot', wrap(async (req, res) => {
  const k = req.ip, a = fAttempts.get(k);
  if (a && a.n >= 5 && Date.now() - a.t < 60 * 60 * 1000) return res.status(429).json({ error: 'Too many requests. Please try again later.' });
  fAttempts.set(k, { n: (a && Date.now() - a.t < 60 * 60 * 1000 ? a.n : 0) + 1, t: a && Date.now() - a.t < 60 * 60 * 1000 ? a.t : Date.now() });
  const username = lc(String(req.body.username || '').trim()), mobile = normMobile(req.body.mobile);
  if (!username || !mobile) return res.status(400).json({ error: 'User ID and mobile number are required' });
  const u = (await pool.query('select id, mobile from users where username=$1', [username])).rows[0];
  if (u) {
    const match = !!u.mobile && last10(u.mobile) === last10(mobile);
    await pool.query(`insert into reset_requests(user_id,mobile,mobile_match) values($1,$2,$3)
      on conflict(user_id) do update set mobile=$2, mobile_match=$3, created_at=now()`, [u.id, mobile, match]);
  }
  res.json({ ok: true });
}));

app.get('/api/resets', auth, admin, wrap(async (req, res) => {
  const r = await pool.query(`select q.id, u.username, u.name, u.mobile registered, q.mobile submitted, q.mobile_match, q.created_at
    from reset_requests q join users u on u.id=q.user_id order by q.created_at desc`);
  res.json({ resets: r.rows });
}));

app.delete('/api/resets/:id', auth, admin, wrap(async (req, res) => {
  await pool.query('delete from reset_requests where id=$1', [parseInt(req.params.id, 10) || 0]);
  res.json({ ok: true });
}));

app.use(express.static(path.join(__dirname, 'public')));
app.use((err, req, res, next) => { console.error(err); res.status(500).json({ error: 'Internal server error' }); });

// Keep-Alive Self-Ping Daemon for Render Free Tier (pings every 10 mins to prevent idle spin-down)
function startKeepAlive() {
  const externalUrl = process.env.RENDER_EXTERNAL_URL || process.env.APP_URL;
  if (!externalUrl) {
    console.log('[Keep-Alive] RENDER_EXTERNAL_URL / APP_URL not set; skipping internal self-ping.');
    return;
  }
  const pingUrl = `${externalUrl.replace(/\/+$/, '')}/ping`;
  const intervalMs = 10 * 60 * 1000; // 10 minutes (Render spins down after 15 mins)

  console.log(`[Keep-Alive] Initialized self-ping daemon targeting ${pingUrl} every 10 minutes`);

  setInterval(async () => {
    try {
      const res = await fetch(pingUrl, {
        headers: { 'User-Agent': 'Render-Self-Ping/1.0' }
      });
      console.log(`[Keep-Alive] Self-ping status: ${res.status}`);
    } catch (err) {
      console.warn(`[Keep-Alive] Self-ping failed:`, err.message);
    }
  }, intervalMs);
}

init().then(() => {
  const port = process.env.PORT || 3000;
  app.listen(port, () => {
    console.log('Running on', port);
    startKeepAlive();
  });
}).catch(e => { console.error(e); process.exit(1); });
