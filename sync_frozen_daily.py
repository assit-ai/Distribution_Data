"""
Automated Poloxy ERP Sync for Daily Update Portal - Frozen Foods Category
Author: Antigravity AI
Paragon Agro Ltd.
"""

import os
import sys
import re
import json
import urllib.request
import urllib.parse
import http.cookiejar
import time
from datetime import datetime, timedelta
import concurrent.futures

import functools
print = functools.partial(print, flush=True)

# Ensure UTF-8 output on Windows console
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    import openpyxl
except ImportError:
    openpyxl = None

BASE_URL = os.environ.get("POLOXY_URL", "http://erp.paragon.com.bd").rstrip('/')
USERNAME = os.environ.get("POLOXY_USER", "a0162e0019")
PASSWORD = os.environ.get("POLOXY_PASS", "pal.123456")

# Target Frozen Food Depots mapping
DEPOT_CONFIG = {
    'tejgaon02': {
        'name': '02. Frozen Foods Tejgaon Depot',
        'godown_id': 'G206',
        'ref_patterns': ['02.TG', '02. TEJGAON', '02.TG-FZ']
    },
    'ctg02': {
        'name': '02. Frozen Foods Chittagong Depot',
        'godown_id': 'G7',
        'ref_patterns': ['02.CTG', '02. CTG', '02.CHITTAGONG']
    },
    'ashulia02': {
        'name': '02. Frozen Foods Factory Godown',
        'godown_id': 'G5',
        'ref_patterns': ['02. FROZEN FOOD', '02.FROZEN FOOD', '02 FACTORY', '02.FACTORY', '02.ASH', 'ASHULIA']
    },
    'mohakhali02': {
        'name': '02. Frozen Foods HO Godown',
        'godown_id': 'G6',
        'ref_patterns': ['02.HO', '02. HO', '02.MHK', 'MOHAKHALI']
    },
    'jessore02': {
        'name': '02. Frozen Foods Jessore Depot',
        'godown_id': 'G256',
        'ref_patterns': ['02 JD', '02.JD', '02.JESSORE', '02. JESSORE']
    },
    'sylhet02': {
        'name': '02. Frozen Foods Sylhet Depot',
        'godown_id': 'G167',
        'ref_patterns': ['02.SYLHET', '02. SYLHET', '02.SYL']
    }
}

def clean_num(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    cleaned = re.sub(r'[^0-9.-]', '', str(v).strip())
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0

def match_depot_from_ref(ref_str, default_depot=None):
    r = str(ref_str or '').upper().strip()
    if not r:
        return default_depot

    for code, conf in DEPOT_CONFIG.items():
        for pat in conf['ref_patterns']:
            if pat in r:
                return code
    return default_depot

class PoloxyClient:
    def __init__(self, base_url=BASE_URL, username=USERNAME, password=PASSWORD):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPRedirectHandler()
        )
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            'Origin': self.base_url
        }

    def authenticate(self):
        print("[*] Authenticating to Poloxy ERP...")
        login_url = f"{self.base_url}/POLOXY/NewLogin"
        data = urllib.parse.urlencode({'uname': self.username, 'pwd': self.password}).encode('utf-8')
        req = urllib.request.Request(login_url, data=data, headers={**self.headers, 'Referer': f"{self.base_url}/POLOXY/jsp/login.jsp"})
        resp = self.opener.open(req, timeout=30)
        html = resp.read().decode('utf-8', errors='ignore')
        if "login.jsp" in resp.url and "Invalid" in html:
            raise RuntimeError("Poloxy login failed! Please check username & password.")
        print(" -> Login successful.")

        # SetContext (Module 10 = Processing)
        set_ctx_url = f"{self.base_url}/POLOXY/SetContext"
        req2 = urllib.request.Request(set_ctx_url, data=urllib.parse.urlencode({'module_id': '10'}).encode('utf-8'),
                                      headers={**self.headers, 'Referer': f"{self.base_url}/POLOXY/jsp/welcome.jsp"})
        resp2 = self.opener.open(req2, timeout=30)
        inputs1 = dict(re.findall(r'<input\s+type=[\'"]hidden[\'"]\s+name=[\'"]([^\'"]+)[\'"]\s+value=[\'"]([^\'"]*)[\'"]',
                                  resp2.read().decode('utf-8', errors='ignore'), re.IGNORECASE))

        # GetContext
        gc_url = f"{self.base_url}/PROCESSINGC/GetContext"
        req3 = urllib.request.Request(gc_url, data=urllib.parse.urlencode(inputs1).encode('utf-8'),
                                      headers={**self.headers, 'Referer': set_ctx_url})
        resp3 = self.opener.open(req3, timeout=30)
        inputs2 = dict(re.findall(r'<input\s+type=[\'"]hidden[\'"]\s+name=[\'"]([^\'"]+)[\'"]\s+value=[\'"]([^\'"]*)[\'"]',
                                  resp3.read().decode('utf-8', errors='ignore'), re.IGNORECASE))

        # SessionTraveller to COMMON
        st_url = f"{self.base_url}/COMMON/SessionTraveller"
        req4 = urllib.request.Request(st_url, data=urllib.parse.urlencode(inputs2).encode('utf-8'),
                                      headers={**self.headers, 'Referer': resp3.url})
        self.opener.open(req4, timeout=30)
        
        # Pre-initialize stock report JSP
        try:
            self.opener.open(urllib.request.Request(f"{self.base_url}/COMMON/r_jsp/godownitemstockreport.jsp", headers=self.headers), timeout=20)
        except Exception:
            pass
        print(" -> ERP Context established.")

    def fetch_godown_stock(self, depot_code, date_str):
        conf = DEPOT_CONFIG[depot_code]
        godown_name = conf['name']
        godown_id = conf['godown_id']

        stock_url = f"{self.base_url}/COMMON/GodownItemStockReport"
        form_data = {
            'item_godown_stock_radio': 'Godownwise',
            'item_stock_chk': '1',
            'data_gdstock_godown': godown_name,
            'gdstock_godown': godown_id,
            'stock_type': '0',
            'data_cstock_item_grp': 'CUT-Up-Part',
            'cstock_item_grp': '17',
            'data_cstock_category_name': '',
            'category_id': '',
            'data_cstock_item_name': '',
            'cstock_item_name': '',
            'fromm': '01/10/2022',
            'too': '31/12/2027',
            'cstock_dummy': 'null',
            'cstock_start_dt': date_str,
            'cstock_end_dt': date_str,
            'max1': 'null'
        }

        req = urllib.request.Request(
            stock_url,
            data=urllib.parse.urlencode(form_data).encode('utf-8'),
            headers={**self.headers, 'Referer': f"{self.base_url}/COMMON/r_jsp/godownitemstockreport.jsp"}
        )

        html = ""
        try:
            resp = self.opener.open(req, timeout=90)
            html = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"    [!] Note: Stock query for {depot_code} ({godown_name}) timed out or busy on ERP ({e}). Defaulting to 0.")
            return 0.0

        total_stock = 0.0
        trs = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL | re.IGNORECASE)
        for tr in trs:
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)]
            if len(cells) >= 13:
                code = cells[0]
                closing_qty = clean_num(cells[12])
                if code and not any(w in code.lower() for w in ['item code', 'total', 'type', 'quantity']):
                    total_stock += closing_qty

        return total_stock

    def fetch_sales_orders(self, date_str):
        print(f"[*] Fetching Sales Orders for {date_str}...")
        so_url = f"{self.base_url}/COMMON/dt_sale_order_status_report"
        form_data = {
            'branch_name': '02. Frozen Foods',
            'hidden_branch_id': 'B0002',
            'customer_name': '',
            'hidd_customer_id': '',
            'start_date': date_str,
            'end_date': date_str,
            'max1': 'null'
        }
        req = urllib.request.Request(
            so_url,
            data=urllib.parse.urlencode(form_data).encode('utf-8'),
            headers={**self.headers, 'Referer': f"{self.base_url}/COMMON/SaleCommission_r_jsp/dt_sale_order_status_report.jsp"}
        )
        resp = self.opener.open(req, timeout=120)
        html = resp.read().decode('utf-8', errors='ignore')

        orders_by_depot = {k: 0.0 for k in DEPOT_CONFIG}
        trs = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL | re.IGNORECASE)
        for tr in trs:
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)]
            if len(cells) < 12 or cells[0] == 'Sr.No.' or 'Internet Explorer' in cells[0]:
                continue
            ref_no = cells[4] if len(cells) > 4 else ''
            bag_qty = clean_num(cells[10]) if len(cells) > 10 else 0.0

            depot = match_depot_from_ref(ref_no)
            if depot and depot in orders_by_depot:
                orders_by_depot[depot] += bag_qty

        return orders_by_depot

    def fetch_delivery_notes(self, start_date_str, end_date_str):
        print(f"[*] Fetching Delivery Notes from {start_date_str} to {end_date_str}...")
        dn_url = f"{self.base_url}/COMMON/Ag_consignee_deliverynote_rpt"
        form_data = {
            'Orderwise': 'Deliverywise',
            'chk1': 'ShowDetails',
            'data_fm_sale_branch': '02. Frozen Foods',
            'fm_sale_branch': 'B0002',
            'fm_sale_start_dt': start_date_str,
            'fm_sale_end_dt': end_date_str,
            'fromm': '01/10/2022',
            'too': '31/12/2027',
            'max1': 'null',
            'fm_sale_dummy': 'null'
        }
        req = urllib.request.Request(
            dn_url,
            data=urllib.parse.urlencode(form_data).encode('utf-8'),
            headers={**self.headers, 'Referer': f"{self.base_url}/COMMON/r_jsp/ag_consignee_deliverynote_report.jsp"}
        )
        resp = self.opener.open(req, timeout=120)
        html = resp.read().decode('utf-8', errors='ignore')

        # rowData JSON extraction
        start_idx = html.find('rowData =[')
        if start_idx == -1:
            start_idx = html.find('rowData = [')
            prefix_len = len('rowData = [')
        else:
            prefix_len = len('rowData =[')

        if start_idx == -1:
            # Fallback to HTML table parse
            return []

        end_idx = html.find('];', start_idx)
        raw_json = html[start_idx + prefix_len:end_idx].strip()
        if not raw_json:
            return []

        try:
            rows = json.loads(f"[{raw_json}]")
            return rows
        except Exception:
            return []


def run_frozen_sync(target_date_str=None, export_excel=False):
    now = datetime.now()
    if not target_date_str:
        target_date_str = now.strftime("%d/%m/%Y")
    
    target_dt = datetime.strptime(target_date_str, "%d/%m/%Y")
    date_iso = target_dt.strftime("%Y-%m-%d")

    # MTD Start Date: 01 of current month
    mtd_start_str = f"01/{target_dt.strftime('%m/%Y')}"
    mtd_days = target_dt.day

    # 7 Days Range
    seven_days_ago_dt = target_dt - timedelta(days=6)
    seven_days_str = seven_days_ago_dt.strftime("%d/%m/%Y")

    print("\n" + "=" * 70)
    print(f"[*] PARAGON AGRO - FROZEN FOODS DAILY SYNC PIPELINE")
    print(f"[*] Target Date : {target_date_str} (MTD: {mtd_start_str} to {target_date_str})")
    print("=" * 70)

    client = PoloxyClient()
    client.authenticate()

    # 1. Fetch Sales Orders for Target Date
    orders_map = client.fetch_sales_orders(target_date_str)

    # 2. Fetch Delivery Notes (From MTD start to today covers MTD, 7-days, and today)
    delivery_rows = client.fetch_delivery_notes(mtd_start_str, target_date_str)
    print(f" -> Found {len(delivery_rows):,} delivery transactions in MTD period.")

    # Location name to depot mapping
    loc_to_depot = {conf['name']: code for code, conf in DEPOT_CONFIG.items()}

    # Aggregate deliveries
    today_delivered_map = {k: 0.0 for k in DEPOT_CONFIG}
    mtd_delivered_map = {k: 0.0 for k in DEPOT_CONFIG}
    seven_d_delivered_map = {k: 0.0 for k in DEPOT_CONFIG}

    cur_entry_date = ""
    for row in delivery_rows:
        ed = str(row.get('entryDate', '')).strip()
        if ed:
            cur_entry_date = ed

        loc = (row.get('location_name') or '').strip()
        depot = loc_to_depot.get(loc)
        if not depot:
            ref_id = row.get('reference_id', '') or row.get('custRefNo', '') or ''
            depot = match_depot_from_ref(ref_id)
        if not depot or depot not in DEPOT_CONFIG:
            continue

        qty = clean_num(row.get('qty', 0.0))
        mtd_delivered_map[depot] += qty

        # Today check (e.g. '05/10/26' or '05/10/2026')
        target_day_month = target_dt.strftime("%d/%m")
        if cur_entry_date.startswith(target_day_month):
            today_delivered_map[depot] += qty

        # 7-day window check
        try:
            parts = cur_entry_date.split('/')
            if len(parts) == 3:
                d_day, d_mon, d_yr = int(parts[0]), int(parts[1]), int(parts[2])
                if d_yr < 100:
                    d_yr += 2000
                row_dt = datetime(d_yr, d_mon, d_day)
                if seven_days_ago_dt <= row_dt <= target_dt:
                    seven_d_delivered_map[depot] += qty
        except Exception:
            pass

    # 3. Fetch Stock for each depot (in parallel for maximum speed)
    print("[*] Querying Godown Stock for each depot in parallel (CUT-Up-Part)...")
    stock_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_to_code = {
            executor.submit(client.fetch_godown_stock, code, target_date_str): code
            for code in DEPOT_CONFIG
        }
        for future in concurrent.futures.as_completed(future_to_code):
            code = future_to_code[future]
            try:
                s = future.result()
            except Exception as e:
                print(f"    [!] Error querying stock for {code}: {e}")
                s = 0.0
            stock_map[code] = s
            print(f" -> Stock ready: {code:<12} = {s:>10,.0f} PKT")

    # 4. Compute Metrics
    summary_data = []
    print("\n" + "=" * 90)
    print(f"{'DEPOT':<12} | {'STOCK (PKT)':<12} | {'ORDERS':<8} | {'DELIVERED':<10} | {'PENDING':<8} | {'MTD AVG':<8} | {'COV(M)':<6} | {'7D AVG':<8} | {'COV(7)':<6}")
    print("-" * 90)

    for code in ['tejgaon02', 'ctg02', 'ashulia02', 'mohakhali02', 'jessore02', 'sylhet02']:
        stock = round(stock_map.get(code, 0.0))
        orders = round(orders_map.get(code, 0.0))
        delivered = round(today_delivered_map.get(code, 0.0))
        pending = max(0, orders - delivered)

        # MTD Avg & Cover
        mtd_tot = mtd_delivered_map.get(code, 0.0)
        mtd_avg = round(mtd_tot / max(1, mtd_days), 1)
        mtd_cov = round(stock / mtd_avg, 1) if mtd_avg > 0 else 999.0

        # 7-Day Avg & Cover
        seven_tot = seven_d_delivered_map.get(code, 0.0)
        seven_avg = round(seven_tot / 7.0, 1)
        seven_cov = round(stock / seven_avg, 1) if seven_avg > 0 else 999.0

        print(f"{code:<12} | {stock:>12,d} | {orders:>8,d} | {delivered:>10,d} | {pending:>8,d} | {mtd_avg:>8.1f} | {mtd_cov:>6.1f} | {seven_avg:>8.1f} | {seven_cov:>6.1f}")

        summary_data.append({
            'depot': code,
            'category': 'Frozen',
            'stock': stock,
            'orders': orders,
            'delivered': delivered,
            'pending': pending,
            'avg_daily_mtd': mtd_avg,
            'stock_cover_mtd': mtd_cov,
            'avg_daily_7d': seven_avg,
            'stock_cover_7d': seven_cov
        })
    print("=" * 90)

    # 5. Export Master Template Excel (Optional)
    if export_excel and openpyxl:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Master_Daily"
        
        # Standard Portal Header
        ws.append(["Depot", "Category", "Orders", "Delivered", "Stock", "Avg_Daily", "Avg_Daily_7D", "Stock_Cover_MTD", "Stock_Cover_7D"])
        for r in summary_data:
            ws.append([
                r['depot'],
                r['category'],
                r['orders'],
                r['delivered'],
                r['stock'],
                r['avg_daily_mtd'],
                r['avg_daily_7d'],
                r['stock_cover_mtd'],
                r['stock_cover_7d']
            ])

        out_excel = f"Master_Daily_Frozen_{date_iso}.xlsx"
        wb.save(out_excel)
        print(f"\n[OK] Ready Master Excel Template generated: {out_excel}")

    return summary_data

if __name__ == '__main__':
    target = None
    json_mode = False
    export_excel = False
    for arg in sys.argv[1:]:
        if arg == '--json':
            json_mode = True
        elif arg == '--excel':
            export_excel = True
        elif not target and not arg.startswith('-'):
            target = arg
    res = run_frozen_sync(target, export_excel=export_excel)
    if json_mode:
        print("\n__JSON_START__" + json.dumps(res) + "__JSON_END__")

