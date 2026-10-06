"""
Automated Poloxy ERP Sync for Daily Update Portal
Categories Supported:
  1. Frozen Foods (02. Frozen Foods) - Unit: Pkt
  2. Process Chicken (01. Process Chicken) - Unit: Kg
  3. Branded Egg (03. Branded Eggs) - Unit: Pcs
Author: Antigravity AI | Paragon Agro Ltd.
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

# Default Built-in Category & Depot Configurations
DEFAULT_CONFIG = {
    'Frozen': {
        'name': '02. Frozen Foods',
        'branch_id': 'B0002',
        'item_group': 'CUT-Up-Part',
        'group_id': '17',
        'unit': 'Pkt',
        'dn_qty_field': 'qty', # Primary unit quantity
        'so_qty_col': 10,      # Bag / primary qty
        'depots': {
            'tejgaon02': {
                'name': '02. Frozen Foods Tejgaon Depot',
                'godown_id': 'G206',
                'ref_patterns': ['02.TG', '02. TEJGAON', '02.TG-FZ', '02 TG', 'TEJGAON']
            },
            'ctg02': {
                'name': '02. Frozen Foods Chittagong Depot',
                'godown_id': 'G7',
                'ref_patterns': ['02.CTG', '02. CTG', '02 CTG', '02.CHITTAGONG', 'CHITTAGONG']
            },
            'ashulia02': {
                'name': '02. Frozen Foods Factory Godown',
                'godown_id': 'G5',
                'ref_patterns': ['02. FROZEN FOOD', '02.FROZEN FOOD', '02 FACTORY', '02.FACTORY', '02.ASH', 'ASHULIA']
            },
            'mohakhali02': {
                'name': '02. Frozen Foods HO Godown',
                'godown_id': 'G6',
                'ref_patterns': ['02.HO', '02. HO', '02 HO', '02.MHK', 'MOHAKHALI']
            },
            'jessore02': {
                'name': '02. Frozen Foods Jessore Depot',
                'godown_id': 'G256',
                'ref_patterns': ['02 JD', '02.JD', '02.JESSORE', '02. JESSORE', 'JESSORE']
            },
            'sylhet02': {
                'name': '02. Frozen Foods Sylhet Depot',
                'godown_id': 'G167',
                'ref_patterns': ['02.SYLHET', '02. SYLHET', '02 SYLHET', '02.SYL', 'SYLHET']
            }
        }
    },
    'Chicken': {
        'name': '01. Process Chicken',
        'branch_id': 'B0001',
        'item_group': 'CUT-Up-Part',
        'group_id': '17',
        'unit': 'Kg',
        'default_depot': 'gazipur01', # If Ref. No. is blank, defaults to Factory
        'dn_qty_field': 'qty',         # Primary unit quantity in Kg
        'so_qty_col': 11,             # Max. Qty.(Kg) column
        'depots': {
            'tejgaon01': {
                'name': '01. Process Tejgaon Depot',
                'godown_id': 'G205',
                'ref_patterns': ['01.TG', '01. TEJGAON', '01 TG', '01.TEJGAON', '01TG']
            },
            'ctg01': {
                'name': '01. Process Chittagong Depot',
                'godown_id': 'G4',
                'ref_patterns': ['01.CTG', '01. CTG', '01 CTG', '01.CHITTAGONG', '01CTG']
            },
            'gazipur01': {
                'name': '01. Process Factory Godown',
                'godown_id': 'G2',
                'ref_patterns': ['01 FACTORY', '01.FACTORY', '01.GAZIPUR', 'GAZIPUR', 'FACTORY']
            },
            'jessore01': {
                'name': '01. Process Jessore Depot',
                'godown_id': 'G255',
                'ref_patterns': ['01 JD', '01.JD', '01.JESSORE', '01 JD', '01JD']
            },
            'sylhet01': {
                'name': '01. Process Sylhet Depot',
                'godown_id': 'G166',
                'ref_patterns': ['01.SYLHET', '01. SYLHET', '01 SYLHET', '01SYL', '01.SYL']
            }
        }
    },
    'Egg': {
        'name': '03. Branded Eggs',
        'branch_id': 'B0003',
        'item_group': 'PACKED EGGS',
        'group_id': '35',
        'unit': 'Pcs',
        'default_depot': 'tejgaon03',
        'dn_qty_field': 'sec_qty',    # Secondary unit quantity in Pcs
        'so_qty_col': 11,             # Max. Qty.(Kg) column is Pcs
        'egg_mode': True,             # Special multiplier rule (12 pcs -> 12x)
        'depots': {
            'tejgaon03': {
                'name': '03. Branded Egg HO Godown',
                'godown_id': 'G9',
                'additional_godowns': [
                    {'name': '03. Branded Egg Tejgaon Depot', 'godown_id': 'G207'}
                ],
                'ref_patterns': [
                    '03HO', '003.HO', '03.HO', '03. HO', '03 HO', '003HO',
                    '03.TG', '03TG', '03. TEJGAON', '03 TEJGAON'
                ]
            }
        }
    }
}

def load_extended_configs():
    """Load any custom or newly added depot mappings from depot_mappings.json (managed by admin web portal)."""
    cfg = dict(DEFAULT_CONFIG)
    mapping_file = os.path.join(os.path.dirname(__file__), 'depot_mappings.json')
    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, 'r', encoding='utf-8') as f:
                custom = json.load(f)
            for cat, details in custom.items():
                if cat in cfg and 'depots' in details:
                    cfg[cat]['depots'].update(details['depots'])
                elif cat not in cfg:
                    cfg[cat] = details
        except Exception as e:
            print(f"[!] Warning: Could not read depot_mappings.json: {e}")
    return cfg

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

def match_depot_from_ref(ref_str, depots_dict, default_depot=None):
    r = str(ref_str or '').upper().strip()
    if not r:
        return default_depot

    # Normalized version: remove spaces, dots, dashes
    clean_r = re.sub(r'[^A-Z0-9]', '', r)

    # 1. Exact match on raw pattern
    for code, conf in depots_dict.items():
        for pat in conf.get('ref_patterns', []):
            if pat.upper() in r:
                return code

    # 2. Normalized alphanumeric check
    for code, conf in depots_dict.items():
        for pat in conf.get('ref_patterns', []):
            clean_pat = re.sub(r'[^A-Z0-9]', '', pat.upper())
            if clean_pat and (clean_pat in clean_r or clean_r in clean_pat):
                return code

    # 3. Code direct match
    for code in depots_dict:
        if code.upper() in clean_r:
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

    def fetch_godown_stock_single(self, godown_name, godown_id, item_group, group_id, date_str, is_egg=False):
        stock_url = f"{self.base_url}/COMMON/GodownItemStockReport"
        form_data = {
            'item_godown_stock_radio': 'Godownwise',
            'item_stock_chk': '1',
            'data_gdstock_godown': godown_name,
            'gdstock_godown': godown_id,
            'stock_type': '0',
            'data_cstock_item_grp': item_group,
            'cstock_item_grp': group_id,
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

        try:
            resp = self.opener.open(req, timeout=90)
            html = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"    [!] Note: Stock query for {godown_name} ({godown_id}) timed out ({e}). Defaulting to 0.")
            return 0.0

        total_stock = 0.0
        trs = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL | re.IGNORECASE)
        for tr in trs:
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)]
            if len(cells) >= 13:
                code = cells[0]
                name = cells[1] if len(cells) > 1 else ''
                closing_qty = clean_num(cells[12])
                if code and not any(w in code.lower() for w in ['item code', 'total', 'type', 'quantity']):
                    if is_egg:
                        # Egg multiplier rule: If SKU has "12 pcs" / "12-pack" / "12 pcs pkt" in name, multiply by 12
                        lower_name = name.lower()
                        if '12 pcs' in lower_name or '12-pack' in lower_name or '12pcs' in lower_name or '12 pkt' in lower_name or '12p' in lower_name:
                            total_stock += (closing_qty * 12.0)
                        else:
                            total_stock += closing_qty
                    else:
                        total_stock += closing_qty

        return total_stock

    def fetch_depot_stock(self, depot_code, depot_conf, cat_conf, date_str):
        godown_name = depot_conf['name']
        godown_id = depot_conf['godown_id']
        grp = cat_conf['item_group']
        grp_id = cat_conf['group_id']
        is_egg = bool(cat_conf.get('egg_mode'))

        tot = self.fetch_godown_stock_single(godown_name, godown_id, grp, grp_id, date_str, is_egg=is_egg)

        # If depot has additional associated godowns (e.g. tejgaon03 HO + Tejgaon Depot)
        for add_g in depot_conf.get('additional_godowns', []):
            try:
                tot += self.fetch_godown_stock_single(add_g['name'], add_g['godown_id'], grp, grp_id, date_str, is_egg=is_egg)
            except Exception as e:
                print(f"    [!] Error querying additional godown {add_g['name']}: {e}")

        return tot

    def fetch_sales_orders(self, cat_conf, start_date_str, end_date_str=None):
        if not end_date_str:
            end_date_str = start_date_str
        branch_name = cat_conf['name']
        branch_id = cat_conf['branch_id']
        qty_col = cat_conf.get('so_qty_col', 10)
        depots_dict = cat_conf['depots']
        default_depot = cat_conf.get('default_depot')

        print(f"[*] Fetching Sales Orders for {branch_name} ({start_date_str} to {end_date_str})...")
        so_url = f"{self.base_url}/COMMON/dt_sale_order_status_report"
        form_data = {
            'branch_name': branch_name,
            'hidden_branch_id': branch_id,
            'customer_name': '',
            'hidd_customer_id': '',
            'start_date': start_date_str,
            'end_date': end_date_str,
            'max1': 'null'
        }
        req = urllib.request.Request(
            so_url,
            data=urllib.parse.urlencode(form_data).encode('utf-8'),
            headers={**self.headers, 'Referer': f"{self.base_url}/COMMON/SaleCommission_r_jsp/dt_sale_order_status_report.jsp"}
        )
        resp = self.opener.open(req, timeout=120)
        html = resp.read().decode('utf-8', errors='ignore')

        orders_by_depot = {k: 0.0 for k in depots_dict}
        trs = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL | re.IGNORECASE)
        for tr in trs:
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)]
            if len(cells) < 12 or cells[0] == 'Sr.No.' or 'Internet Explorer' in cells[0]:
                continue
            ref_no = cells[4] if len(cells) > 4 else ''
            qty = clean_num(cells[qty_col]) if len(cells) > qty_col else 0.0

            depot = match_depot_from_ref(ref_no, depots_dict, default_depot=default_depot)
            if depot and depot in orders_by_depot:
                orders_by_depot[depot] += qty

        return orders_by_depot

    def fetch_delivery_notes(self, cat_conf, start_date_str, end_date_str):
        branch_name = cat_conf['name']
        branch_id = cat_conf['branch_id']
        print(f"[*] Fetching Delivery Notes for {branch_name} from {start_date_str} to {end_date_str}...")

        dn_url = f"{self.base_url}/COMMON/Ag_consignee_deliverynote_rpt"
        form_data = {
            'Orderwise': 'Deliverywise',
            'chk1': 'ShowDetails',
            'data_fm_sale_branch': branch_name,
            'fm_sale_branch': branch_id,
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

        start_idx = html.find('rowData =[')
        if start_idx == -1:
            start_idx = html.find('rowData = [')
            prefix_len = len('rowData = [')
        else:
            prefix_len = len('rowData =[')

        if start_idx == -1:
            return []

        end_idx = html.find('];', start_idx)
        raw_json = html[start_idx + prefix_len:end_idx].strip()
        if not raw_json:
            return []

        try:
            return json.loads(f"[{raw_json}]")
        except Exception:
            return []


def run_category_sync(client, cat_key, cat_conf, start_date_str, end_date_str, start_dt, end_dt):
    depots_dict = cat_conf['depots']
    default_depot = cat_conf.get('default_depot')
    qty_field = cat_conf.get('dn_qty_field', 'qty')
    unit = cat_conf.get('unit', 'Qty')

    is_single_day = (start_date_str == end_date_str)
    num_days = max(1, (end_dt - start_dt).days + 1)

    print(f"\n{'='*70}")
    print(f"[*] PROCESSING CATEGORY: {cat_key.upper()} ({cat_conf['name']}) [Unit: {unit}]")
    if is_single_day:
        print(f"[*] Report Mode: SINGLE DATE ({start_date_str}) [Exact Date Extraction]")
    else:
        print(f"[*] Report Mode: DATE RANGE ({start_date_str} to {end_date_str}) [{num_days} Days]")
    print(f"{'='*70}")

    # 1. Fetch Sales Orders for Exact Date / Range
    orders_map = client.fetch_sales_orders(cat_conf, start_date_str, end_date_str)

    # 2. Fetch Delivery Notes for Exact Date / Range (Direct ERP query for requested period)
    delivery_rows = client.fetch_delivery_notes(cat_conf, start_date_str, end_date_str)
    print(f" -> Found {len(delivery_rows):,} delivery transactions between {start_date_str} and {end_date_str} for {cat_key}.")

    # Name mapping
    loc_to_depot = {}
    for code, conf in depots_dict.items():
        loc_to_depot[conf['name']] = code
        for add_g in conf.get('additional_godowns', []):
            loc_to_depot[add_g['name']] = code

    delivered_map = {k: 0.0 for k in depots_dict}
    for row in delivery_rows:
        loc = (row.get('location_name') or '').strip()
        depot = loc_to_depot.get(loc)
        if not depot:
            ref_id = row.get('reference_id', '') or row.get('custRefNo', '') or ''
            depot = match_depot_from_ref(ref_id, depots_dict, default_depot=default_depot)
        if not depot or depot not in depots_dict:
            continue

        # Extract quantity based on category field (Primary 'qty' or Secondary 'sec_qty')
        qty = clean_num(row.get(qty_field, 0.0))
        if qty == 0.0 and qty_field != 'qty':
            qty = clean_num(row.get('qty', 0.0)) # Fallback

        delivered_map[depot] += qty

    # 3. Fetch Stock as of end_date_str
    print(f"[*] Querying Godown Stock as of {end_date_str} for each depot ({cat_conf['item_group']})...")
    stock_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(depots_dict))) as executor:
        future_to_code = {
            executor.submit(client.fetch_depot_stock, code, conf, cat_conf, end_date_str): code
            for code, conf in depots_dict.items()
        }
        for future in concurrent.futures.as_completed(future_to_code):
            code = future_to_code[future]
            try:
                s = future.result()
            except Exception as e:
                print(f"    [!] Error querying stock for {code}: {e}")
                s = 0.0
            stock_map[code] = s
            print(f" -> Stock ready: {code:<12} = {s:>10,.1f} {unit}")

    # 4. Compute Metrics
    summary_data = []
    print("\n" + "-" * 88)
    print(f"{'DEPOT':<12} | {'STOCK (' + unit + ')':<14} | {'ORDERS':<10} | {'DELIVERED':<12} | {'PENDING':<10} | {'AVG DAILY':<10} | {'COVER(D)':<8}")
    print("-" * 88)

    for code in depots_dict:
        stock = round(stock_map.get(code, 0.0), 1 if unit == 'Kg' else 0)
        orders = round(orders_map.get(code, 0.0), 1 if unit == 'Kg' else 0)
        delivered = round(delivered_map.get(code, 0.0), 1 if unit == 'Kg' else 0)
        pending = max(0.0, round(orders - delivered, 1 if unit == 'Kg' else 0))

        avg_daily = round(delivered / num_days, 1) if num_days > 0 else delivered
        cover = round(stock / avg_daily, 1) if avg_daily > 0 else 999.0

        print(f"{code:<12} | {stock:>14,.1f} | {orders:>10,.1f} | {delivered:>12,.1f} | {pending:>10,.1f} | {avg_daily:>10.1f} | {cover:>8.1f}")

        summary_data.append({
            'depot': code,
            'category': cat_key,
            'unit': unit,
            'entry_date': end_dt.strftime("%Y-%m-%d"),
            'stock': stock,
            'orders': orders,
            'delivered': delivered,
            'pending': pending,
            'avg_daily': avg_daily,
            'stock_cover': cover
        })

    print("-" * 88)
    return summary_data


def run_sync_pipeline(start_date_str=None, end_date_str=None, category_filter='all', export_excel=False):
    now = datetime.now()
    if not start_date_str:
        start_date_str = now.strftime("%d/%m/%Y")
    if not end_date_str:
        end_date_str = start_date_str

    start_dt = datetime.strptime(start_date_str, "%d/%m/%Y")
    end_dt = datetime.strptime(end_date_str, "%d/%m/%Y")
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
        start_date_str, end_date_str = end_date_str, start_date_str

    configs = load_extended_configs()

    print("\n" + "=" * 70)
    print(f"[*] PARAGON AGRO - DAILY ERP AUTO SYNC PIPELINE")
    if start_date_str == end_date_str:
        print(f"[*] Target Date : {start_date_str} (Single Exact Date)")
    else:
        print(f"[*] Date Range  : {start_date_str} to {end_date_str}")
    print(f"[*] Categories  : {category_filter.upper()}")
    print("=" * 70)

    client = PoloxyClient()
    client.authenticate()

    cats_to_run = []
    if category_filter.lower() in ('all', ''):
        cats_to_run = ['Frozen', 'Chicken', 'Egg']
    else:
        for c in configs:
            if c.lower() == category_filter.lower():
                cats_to_run.append(c)

    if not cats_to_run:
        print(f"[!] Warning: Category '{category_filter}' not found in configuration. Defaulting to all.")
        cats_to_run = ['Frozen', 'Chicken', 'Egg']

    all_results = []
    for ckey in cats_to_run:
        if ckey in configs:
            res = run_category_sync(client, ckey, configs[ckey], start_date_str, end_date_str, start_dt, end_dt)
            all_results.extend(res)

    # Export Master Excel Template (Optional)
    if export_excel and openpyxl:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Master_Daily"
        ws.append(["Depot", "Category", "Unit", "Orders", "Delivered", "Pending", "Stock", "Avg_Daily", "Stock_Cover"])
        for r in all_results:
            ws.append([
                r['depot'],
                r['category'],
                r.get('unit', 'Qty'),
                r['orders'],
                r['delivered'],
                r['pending'],
                r['stock'],
                r['avg_daily'],
                r['stock_cover']
            ])
        date_tag = end_dt.strftime("%Y-%m-%d")
        out_excel = f"Master_Daily_Combined_{date_tag}.xlsx"
        wb.save(out_excel)
        print(f"\n[OK] Ready Master Excel generated: {out_excel}")

    return all_results

# Backward compatibility alias
def run_frozen_sync(target_date_str=None, export_excel=False):
    return run_sync_pipeline(start_date_str=target_date_str, end_date_str=target_date_str, category_filter='Frozen', export_excel=export_excel)

if __name__ == '__main__':
    start_date = None
    end_date = None
    json_mode = False
    export_excel = False
    category_filter = 'all'

    pos_args = []
    for arg in sys.argv[1:]:
        if arg == '--json':
            json_mode = True
        elif arg == '--excel':
            export_excel = True
        elif arg.startswith('--category='):
            category_filter = arg.split('=', 1)[1].strip()
        elif arg.startswith('--start='):
            start_date = arg.split('=', 1)[1].strip()
        elif arg.startswith('--end='):
            end_date = arg.split('=', 1)[1].strip()
        elif not arg.startswith('-'):
            pos_args.append(arg)

    if pos_args:
        if not start_date:
            start_date = pos_args[0]
        if len(pos_args) > 1 and not end_date:
            end_date = pos_args[1]

    if not end_date:
        end_date = start_date

    res = run_sync_pipeline(start_date, end_date, category_filter=category_filter, export_excel=export_excel)
    if json_mode:
        print("\n__JSON_START__" + json.dumps(res) + "__JSON_END__")
