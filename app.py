import requests
import sqlite3
import time
import functools
import os
from datetime import datetime, date, timedelta
from flask import Flask, render_template, jsonify, request, send_from_directory

app = Flask(__name__)

# --- CONFIGURATION ---
URL = "https://api.trycrew.com/willow/graphql"
# In app.py
DB_FILE = os.environ.get("DB_FILE", "savings_data.db")

# --- CACHING SYSTEM ---
class SimpleCache:
    def __init__(self, ttl_seconds=300):
        self.store = {}
        self.ttl = ttl_seconds

    def get(self, key):
        if key in self.store:
            timestamp, data = self.store[key]
            if time.time() - timestamp < self.ttl:
                return data
            else:
                del self.store[key]  # Expired
        return None

    def set(self, key, data):
        self.store[key] = (time.time(), data)

    def clear(self):
        self.store = {}

cache = SimpleCache(ttl_seconds=300)

def cached(key_prefix):
    """Decorator to cache function results. Supports force_refresh=True kwarg."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            force_refresh = kwargs.pop('force_refresh', False)
            key_parts = [key_prefix] + [str(arg) for arg in args] + [f"{k}={v}" for k, v in kwargs.items()]
            cache_key = ":".join(key_parts)
            
            if not force_refresh:
                cached_data = cache.get(cache_key)
                if cached_data:
                    print(f"⚡ Serving {key_prefix} from cache")
                    return cached_data
            
            print(f"🌐 Fetching {key_prefix} from API (Fresh)...")
            result = func(*args, **kwargs)
            
            if isinstance(result, dict) and "error" not in result:
                cache.set(cache_key, result)
            return result
        return wrapper
    return decorator

# --- DATABASE FUNCTIONS ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history (date TEXT PRIMARY KEY, balance REAL)''')
    conn.commit()
    conn.close()

def log_balance(balance):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        c.execute("INSERT OR REPLACE INTO history (date, balance) VALUES (?, ?)", (today, balance))
        conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()

def get_history():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT date, balance FROM history ORDER BY date ASC")
    data = c.fetchall()
    conn.close()
    return {
        "labels": [row[0] for row in data],
        "values": [row[1] for row in data]
    }

# --- API HELPERS ---
def get_crew_headers():
    # We now look for Environment Variables provided by Docker
    bearer_token = os.environ.get("BEARER_TOKEN")


    return {
        "accept": "*/*",
        "content-type": "application/json",
        "authorization": bearer_token,
        "user-agent": "Crew/1 CFNetwork/3860.300.31 Darwin/25.2.0",
    }

# --- DATA FETCHERS ---
@cached("primary_account_id")
def get_primary_account_id():
    try:
        headers = get_crew_headers()
        if not headers: return None
        query_string = """ query CurrentUser { currentUser { accounts { id displayName } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()
        accounts = data.get("data", {}).get("currentUser", {}).get("accounts", [])
        for acc in accounts:
            if acc.get("displayName") == "Checking":
                return acc.get("id")
        if accounts: return accounts[0].get("id")
        return None
    except Exception as e:
        print(f"Error fetching Account ID: {e}")
        return None

@cached("financial_data")
def get_financial_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ query CurrentUser { currentUser { accounts { subaccounts { id goal overallBalance name } } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()
        results = {}
        for account in data.get("data", {}).get("currentUser", {}).get("accounts", []):
            for sub in account.get("subaccounts", []):
                name = sub.get("name")
                balance_raw = sub.get("overallBalance", 0) / 100.0
                if name == "Savings":
                    goal = sub.get("goal", 0) / 100.0 if sub.get("goal") else 0
                    log_balance(balance_raw)
                    results["savings"] = {"id": sub.get("id"), "name": name, "balance": f"${balance_raw:.2f}", "raw_val": balance_raw, "goal": f"${round(goal)}"}
                elif name == "Checking":
                    results["checking"] = {"name": name, "balance": f"${balance_raw:.2f}", "raw_balance": balance_raw}
        return results if results else {"error": "No relevant accounts found"}
    except Exception as e:
        return {"error": str(e)}

@cached("transactions")
def get_transactions_data(search_term=None, min_date=None, max_date=None, min_amount=None, max_amount=None):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        account_id = get_primary_account_id()
        if not account_id: return {"error": "Could not find Checking Account ID"}
        query_string = """ query RecentActivity($accountId: ID!, $cursor: String, $pageSize: Int = 100, $searchFilters: CashTransactionFilter) { account: node(id: $accountId) { ... on Account { id cashTransactions(first: $pageSize, after: $cursor, searchFilters: $searchFilters) { edges { node { id amount description occurredAt title type subaccount { id } } } } } } } """
        filters = {}
        if search_term: filters["fuzzySearch"] = search_term
        variables = {"pageSize": 100, "accountId": account_id, "searchFilters": filters}
        response = requests.post(URL, headers=headers, json={"operationName": "RecentActivity", "variables": variables, "query": query_string})
        if response.status_code != 200: return {"error": f"API Error: {response.text}"}
        data = response.json()
        if 'errors' in data: return {"error": data['errors'][0]['message']}
        txs = []
        try:
            edges = data.get('data', {}).get('account', {}).get('cashTransactions', {}).get('edges', [])
            for edge in edges:
                node = edge['node']
                amt = node['amount'] / 100.0
                date_str = node['occurredAt']
                sub_id = node.get('subaccount', {}).get('id') if node.get('subaccount') else None
                if min_date or max_date:
                    tx_date = date_str[:10]
                    if min_date and tx_date < min_date: continue
                    if max_date and tx_date > max_date: continue
                if min_amount or max_amount:
                    abs_amt = abs(amt)
                    if min_amount and abs_amt < float(min_amount): continue
                    if max_amount and abs_amt > float(max_amount): continue
                txs.append({"id": node['id'], "title": node['title'], "description": node['description'], "amount": amt, "date": date_str, "type": node['type'], "subaccountId": sub_id})
        except Exception as e:
            return {"error": f"Parse Error: {str(e)}"}
        return {"transactions": txs}
    except Exception as e:
        return {"error": str(e)}

@cached("tx_detail")
def get_transaction_detail(activity_id):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ query ActivityDetail($activityId: ID!, $isTransfer: Boolean = false) { cashTransaction: node(id: $activityId) @skip(if: $isTransfer) { ... on CashTransaction { ...CashTransactionActivity __typename } __typename } pendingTransfer: node(id: $activityId) @include(if: $isTransfer) { ... on Transfer { ...PendingTransferActivity __typename } __typename } } fragment CashTransactionFields on CashTransaction { id amount avatarFallbackColor currencyCode description externalMemo imageUrl isSplit note occurredAt quickCleanName ruleSuggestionString status title type __typename } fragment NameableAccount on Account { id displayName belongsToCurrentUser isChildAccount isExternalAccount avatarUrl icon type mask owner { displayName avatarUrl avatarColor __typename } __typename } fragment NameableSubaccount on Subaccount { id type belongsToCurrentUser isChildAccount isExternalAccount displayName avatarUrl icon piggyBanked isPrimary status account { id __typename } owner { displayName avatarUrl avatarColor __typename } primaryOwner { id __typename } __typename } fragment NameableCashTransaction on CashTransaction { __typename id amount description externalMemo avatarFallbackColor imageUrl quickCleanName title type account { ...NameableAccount __typename } subaccount { ...NameableSubaccount __typename } } fragment RelatedTransactions on CashTransaction { id status occurredAt relatedTransactions { id occurredAt __typename } transfer { id type status scheduledSettlement __typename } __typename } fragment TransferFields on Transfer { id amount formattedErrorCode isCancellable note occurredAt scheduledSettlement status type accountFrom { ...NameableAccount __typename } accountTo { ...NameableAccount __typename } subaccountFrom { ...NameableSubaccount __typename } subaccountTo { ...NameableSubaccount __typename } permittedActions { transferReassign __typename } __typename } fragment CashTransactionActivity on CashTransaction { ...CashTransactionFields ...NameableCashTransaction ...RelatedTransactions account { id subaccounts { id belongsToCurrentUser clearedBalance displayName isExternalAccount owner { displayName __typename } __typename } __typename } latestDebitCardTransactionDetail { id merchantAddress1 merchantCity merchantCountry merchantName merchantState merchantZip __typename } debitCard { id name type cardOwner: user { id displayedFirstName __typename } __typename } transfer { ...TransferFields accountTo { id primaryOwner { id displayedFirstName __typename } __typename } __typename } subaccount { id displayName __typename } permittedActions { cashTransactionReassign cashTransactionSplit cashTransactionUndo __typename } __typename } fragment PendingTransferActivity on Transfer { ...TransferFields __typename } """
        variables = {"isTransfer": False, "activityId": activity_id}
        response = requests.post(URL, headers=headers, json={"operationName": "ActivityDetail", "variables": variables, "query": query_string})
        data = response.json()
        node = data.get('data', {}).get('cashTransaction') or data.get('data', {}).get('pendingTransfer')
        if not node: return {"error": "Details not found"}
        merchant_info = node.get('latestDebitCardTransactionDetail') or {}
        return {"id": node.get('id'), "amount": node.get('amount', 0) / 100.0, "title": node.get('title'), "description": node.get('description'), "status": node.get('status'), "date": node.get('occurredAt'), "memo": node.get('externalMemo'), "merchant": {"name": merchant_info.get('merchantName'), "address": merchant_info.get('merchantAddress1'), "city": merchant_info.get('merchantCity'), "state": merchant_info.get('merchantState'), "zip": merchant_info.get('merchantZip')}}
    except Exception as e:
        return {"error": str(e)}

@cached("expenses")
def get_expenses_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        # Updated query to include funding settings
        query_string = """ 
        query CurrentUser { 
            currentUser { 
                accounts { 
                    billReserve { 
                        nextFundingDate 
                        totalReservedAmount 
                        estimatedNextFundingAmount 
                        settings { 
                            funding { 
                                subaccount { 
                                    displayName 
                                } 
                            } 
                        }
                        bills { 
                            amount 
                            anchorDate 
                            autoAdjustAmount 
                            dayOfMonth 
                            daysOverdue 
                            estimatedNextFundingAmount 
                            frequency 
                            frequencyInterval 
                            id 
                            name 
                            paused 
                            reservedAmount 
                            reservedBy 
                            status 
                        } 
                    } 
                } 
            } 
        } 
        """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()
        accounts = data.get("data", {}).get("currentUser", {}).get("accounts", [])
        
        all_bills = []
        summary = {}
        
        for acc in accounts:
            bill_reserve = acc.get("billReserve")
            if bill_reserve:
                # Extract funding source name safely
                funding_name = "Checking"
                try:
                    funding_name = bill_reserve["settings"]["funding"]["subaccount"]["displayName"]
                except (KeyError, TypeError):
                    pass

                summary = {
                    "totalReserved": (bill_reserve.get("totalReservedAmount") or 0) / 100.0, 
                    "nextFundingDate": bill_reserve.get("nextFundingDate"), 
                    "estimatedFunding": (bill_reserve.get("estimatedNextFundingAmount") or 0) / 100.0,
                    "fundingSource": funding_name # <--- Added this
                }
                
                bills = bill_reserve.get("bills", [])
                for b in bills:
                    amt = (b.get("amount") or 0) / 100.0
                    res = (b.get("reservedAmount") or 0) / 100.0
                    est_fund = (b.get("estimatedNextFundingAmount") or 0) / 100.0
                    all_bills.append({
                        "id": b.get("id"), 
                        "name": b.get("name"), 
                        "amount": amt, 
                        "reserved": res, 
                        "estimatedFunding": est_fund, 
                        "frequency": b.get("frequency"), 
                        "dueDay": b.get("dayOfMonth"), 
                        "paused": b.get("paused"), 
                        "reservedBy": b.get("reservedBy")
                    })
        
        all_bills.sort(key=lambda x: x['reservedBy'] or "9999-12-31")
        return {"expenses": all_bills, "summary": summary}
    except Exception as e:
        return {"error": str(e)}
        
@cached("goals")
def get_goals_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ query CurrentUser { currentUser { accounts { subaccounts { goal overallBalance name id } } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()
        goals = []
        for account in data.get("data", {}).get("currentUser", {}).get("accounts", []):
            for sub in account.get("subaccounts", []):
                name = sub.get("name")
                if name != "Checking":
                    balance = sub.get("overallBalance", 0) / 100.0
                    target = sub.get("goal", 0) / 100.0 if sub.get("goal") else 0
                    goals.append({"id": sub.get("id"), "name": name, "balance": balance, "target": target, "status": "Active"})
        return {"goals": goals}
    except Exception as e:
        return {"error": str(e)}

@cached("trends")
def get_monthly_trends():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        account_id = get_primary_account_id()
        if not account_id: return {"error": "Could not find Checking Account ID"}
        today = date.today()
        start_of_month = date(today.year, today.month, 1).strftime("%Y-%m-%dT00:00:00Z")
        query_string = """ query RecentActivity($accountId: ID!, $cursor: String, $pageSize: Int = 100) { account: node(id: $accountId) { ... on Account { cashTransactions(first: $pageSize, after: $cursor) { edges { node { amount occurredAt } } } } } } """
        variables = {"pageSize": 100, "accountId": account_id}
        response = requests.post(URL, headers=headers, json={"operationName": "RecentActivity", "variables": variables, "query": query_string})
        data = response.json()
        edges = data.get('data', {}).get('account', {}).get('cashTransactions', {}).get('edges', [])
        earned = 0.0
        spent = 0.0
        for edge in edges:
            node = edge['node']
            tx_date = node['occurredAt']
            amount = node['amount'] / 100.0
            if tx_date >= start_of_month:
                if amount > 0:
                    earned += amount
                else:
                    spent += abs(amount)
        return {"earned": earned, "spent": spent}
    except Exception as e:
        return {"error": str(e)}

@cached("subaccounts")
def get_subaccounts_list():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ query CurrentUser { currentUser { accounts { subaccounts { id name overallBalance } } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()
        subs = []
        for account in data.get("data", {}).get("currentUser", {}).get("accounts", []):
            for sub in account.get("subaccounts", []):
                balance = sub.get("overallBalance", 0) / 100.0
                subs.append({"id": sub.get("id"), "name": sub.get("name"), "balance": balance})
        return {"subaccounts": subs}
    except Exception as e:
        return {"error": str(e)}

def move_money(from_id, to_id, amount, note=""):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ mutation InitiateTransferScottie($input: InitiateTransferInput!) { initiateTransfer(input: $input) { result { id __typename } __typename } } """
        amount_cents = int(float(amount) * 100)
        variables = {"input": {"amount": amount_cents, "accountFromId": from_id, "accountToId": to_id, "note": note or "Transfer"}}
        response = requests.post(URL, headers=headers, json={"operationName": "InitiateTransferScottie", "variables": variables, "query": query_string})
        data = response.json()
        if 'errors' in data: return {"error": data['errors'][0]['message']}
        print("🧹 Clearing Cache after transaction...")
        cache.clear()
        return {"success": True, "result": data.get("data", {}).get("initiateTransfer", {})}
    except Exception as e:
        return {"error": str(e)}

@cached("family")
def get_family_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ query FamilyScreen { currentUser { id family { id children { id dob cardColor imageUrl displayedFirstName spendAccount { id overallBalance subaccounts { id displayName clearedBalance } } scheduledAllowance { id totalAmount } } parents { id isApplying cardColor imageUrl displayedFirstName } } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "FamilyScreen", "query": query_string})
        data = response.json()
        family_node = data.get("data", {}).get("currentUser", {}).get("family", {})
        children = []
        for child in family_node.get("children", []):
            balance = child.get("spendAccount", {}).get("overallBalance", 0) / 100.0
            allowance = "Not set"
            if child.get("scheduledAllowance"):
                amt = child["scheduledAllowance"].get("totalAmount", 0) / 100.0
                allowance = f"${amt:.2f}/week"
            children.append({"id": child.get("id"), "name": child.get("displayedFirstName"), "image": child.get("imageUrl"), "color": child.get("cardColor"), "dob": child.get("dob"), "balance": balance, "allowance": allowance, "role": "Child"})
        parents = []
        for parent in family_node.get("parents", []):
            parents.append({"id": parent.get("id"), "name": parent.get("displayedFirstName"), "image": parent.get("imageUrl"), "color": parent.get("cardColor"), "role": "Parent"})
        return {"children": children, "parents": parents}
    except Exception as e:
        return {"error": str(e)}

def create_pocket(name, target_amount, initial_amount, note):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        # Get the main Account ID automatically
        account_id = get_primary_account_id()
        if not account_id: return {"error": "Could not find Checking Account ID"}

        query_string = """
        mutation CreateSubaccount($input: CreateSubaccountInput!) {
            createSubaccount(input: $input) {
                result {
                    id
                    name
                    balance
                    goal
                    status
                    subaccountType
                }
            }
        }
        """
        
        # Convert amounts to cents (assuming API expects cents based on your move_money logic)
        target_cents = int(float(target_amount) * 100)
        initial_cents = int(float(initial_amount) * 100)

        variables = {
            "input": {
                "type": "SAVINGS",           # Hardcoded per instructions
                "piggyBanked": False,        # Hardcoded per instructions
                "accountId": account_id,     # Auto-filled
                "name": name,
                "targetAmount": target_cents,
                "initialTransferAmount": initial_cents,
                "note": note
            }
        }

        response = requests.post(URL, headers=headers, json={
            "operationName": "CreateSubaccount",
            "variables": variables,
            "query": query_string
        })

        data = response.json()
        
        if 'errors' in data:
            return {"error": data['errors'][0]['message']}
            
        # Clear cache so the new pocket appears immediately
        print("🧹 Clearing Cache after pocket creation...")
        cache.clear()
        
        return {"success": True, "result": data.get("data", {}).get("createSubaccount", {}).get("result")}

    except Exception as e:
        return {"error": str(e)}

def get_cards_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_phys = """ query PhysicalCards { currentUser { family { children { activePhysicalDebitCard { ...PhysicalDebitCardFields } issuingPhysicalDebitCard { ...PhysicalDebitCardFields } } parents { activePhysicalDebitCard { ...PhysicalDebitCardFields } issuingPhysicalDebitCard { ...PhysicalDebitCardFields } } } } } fragment PhysicalDebitCardFields on DebitCard { id color status lastFour user { firstName } } """
        query_virt = """ query VirtualCards { currentUser { family { children { virtualDebitCards { ...VirtualDebitCardFields } } parents { virtualDebitCards { ...VirtualDebitCardFields } } } } } fragment VirtualDebitCardFields on DebitCard { id type color status lastFour name monthlyLimit user { firstName } } """
        res_phys = requests.post(URL, headers=headers, json={"operationName": "PhysicalCards", "query": query_phys})
        res_virt = requests.post(URL, headers=headers, json={"operationName": "VirtualCards", "query": query_virt})
        data_phys = res_phys.json()
        data_virt = res_virt.json()
        all_cards = []
        fam_phys = data_phys.get("data", {}).get("currentUser", {}).get("family", {}) or {}
        people_phys = (fam_phys.get("children") or []) + (fam_phys.get("parents") or [])
        for person in people_phys:
            card = person.get("activePhysicalDebitCard")
            if card: all_cards.append({"id": card.get("id"), "type": "Physical", "name": "Simple Visa® Card", "holder": card.get("user", {}).get("firstName"), "last4": card.get("lastFour"), "color": card.get("color"), "status": card.get("status"), "limit": None})
            issuing = person.get("issuingPhysicalDebitCard")
            if issuing: all_cards.append({"id": issuing.get("id"), "type": "Physical", "name": "Simple Visa® Card (Issuing)", "holder": issuing.get("user", {}).get("firstName"), "last4": issuing.get("lastFour") or "????", "color": issuing.get("color"), "status": issuing.get("status"), "limit": None})
        fam_virt = data_virt.get("data", {}).get("currentUser", {}).get("family", {}) or {}
        people_virt = (fam_virt.get("children") or []) + (fam_virt.get("parents") or [])
        for person in people_virt:
            v_cards = person.get("virtualDebitCards", [])
            for card in v_cards:
                limit_raw = card.get("monthlyLimit")
                limit_str = f"${limit_raw / 100:.0f}/mo" if limit_raw else "No Limit"
                all_cards.append({"id": card.get("id"), "type": "Virtual", "name": card.get("name") or "Virtual Card", "holder": card.get("user", {}).get("firstName"), "last4": card.get("lastFour"), "color": card.get("color"), "status": card.get("status"), "limit": limit_str})
        return {"cards": all_cards}
    except Exception as e:
        print(f"Card Error: {e}")
        return {"error": str(e)}


def delete_subaccount_action(sub_id):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}

        # exact mutation requested
        query_string = """
        mutation DeleteSubaccount($id: ID!) {
            deleteSubaccount(input: { subaccountId: $id }) {
                result {
                    avatarUrl
                    balance
                    belongsToCurrentUser
                    clearedBalance
                    displayName
                    externalLinkNeedsRepair
                    goal
                    hidden
                    icon
                    id
                    isChildAccount
                    isExternalAccount
                    isPrimary
                    isWireRecipientAccount
                    name
                    note
                    overallBalance
                    piggyBanked
                    shouldPollBalance
                    status
                    subaccountType
                    targetAmount
                    type
                }
            }
        }
        """
        
        variables = {"id": sub_id}

        response = requests.post(URL, headers=headers, json={
            "operationName": "DeleteSubaccount",
            "variables": variables,
            "query": query_string
        })

        data = response.json()
        
        if 'errors' in data:
            return {"error": data['errors'][0]['message']}
            
        print("🧹 Clearing Cache after deletion...")
        cache.clear()
        
        return {"success": True, "result": data.get("data", {}).get("deleteSubaccount", {}).get("result")}

    except Exception as e:
        return {"error": str(e)}

def delete_bill_action(bill_id):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}

        # Mutation based on your input
        query_string = """
        mutation DeleteBill($id: ID!) {
            deleteBill(input: { billId: $id }) {
                result {
                    id
                    status
                    name
                }
            }
        }
        """
        
        variables = {"id": bill_id}

        response = requests.post(URL, headers=headers, json={
            "operationName": "DeleteBill",
            "variables": variables,
            "query": query_string
        })

        data = response.json()
        
        if 'errors' in data:
            return {"error": data['errors'][0]['message']}
            
        print("🧹 Clearing Cache after bill deletion...")
        cache.clear()
        
        return {"success": True, "result": data.get("data", {}).get("deleteBill", {}).get("result")}

    except Exception as e:
        return {"error": str(e)}

# Add this helper function to fetch the specific funding source name
def get_bill_funding_source():
    try:
        headers = get_crew_headers()
        if not headers: return "Checking"

        query_string = """
        query CurrentUser {
            currentUser {
                accounts {
                    billReserve {
                        settings {
                            funding {
                                subaccount {
                                    displayName
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        
        response = requests.post(URL, headers=headers, json={
            "operationName": "CurrentUser",
            "query": query_string
        })

        data = response.json()
        
        # Parse logic to find the active billReserve
        # We ignore 'errors' regarding nullables and just look for valid data
        accounts = data.get("data", {}).get("currentUser", {}).get("accounts", [])
        
        for acc in accounts:
            # We look for the first account that has a non-null billReserve
            if acc and acc.get("billReserve"):
                try:
                    return acc["billReserve"]["settings"]["funding"]["subaccount"]["displayName"]
                except (KeyError, TypeError):
                    continue
                    
        return "Checking" # Default fallback
    except Exception:
        return "Checking"


# Update the main action to use the helper
def create_bill_action(name, amount, frequency_key, day_of_month, match_string=None, min_amt=None, max_amt=None, is_variable=False):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        account_id = get_primary_account_id()
        if not account_id: return {"error": "Main Account ID not found"}

        # --- 1. Map Frequency & Interval ---
        freq_map = {
            "WEEKLY":        ("WEEKLY", 1),
            "BIWEEKLY":      ("WEEKLY", 2),
            "MONTHLY":       ("MONTHLY", 1),
            "QUARTERLY":     ("MONTHLY", 3),
            "SEMI_ANNUALLY": ("MONTHLY", 6),
            "ANNUALLY":      ("YEARLY", 1)
        }
        
        if frequency_key not in freq_map:
            return {"error": "Invalid frequency selected"}
            
        final_freq, final_interval = freq_map[frequency_key]

        # --- 2. Calculate Anchor Date ---
        today = date.today()
        last_day_prev_month = today.replace(day=1) - timedelta(days=1)
        try:
            anchor_date_obj = last_day_prev_month.replace(day=int(day_of_month))
        except ValueError:
            anchor_date_obj = last_day_prev_month
            
        anchor_date_str = anchor_date_obj.strftime("%Y-%m-%d")

        # --- 3. Build Reassignment Rule ---
        reassignment_rule = None
        if match_string:
            rule = {"match": match_string}
            if min_amt: rule["minAmount"] = int(float(min_amt) * 100)
            if max_amt: rule["maxAmount"] = int(float(max_amt) * 100)
            reassignment_rule = rule

        # --- 4. Mutation (Simplified, as we fetch name separately now) ---
        query_string = """
        mutation CreateBill($input: CreateBillInput!) {
            createBill(input: $input) {
                result {
                    id
                    name
                    status
                    amount
                    reservedAmount
                }
            }
        }
        """
        
        variables = {
            "input": {
                "accountId": account_id,
                "amount": int(float(amount) * 100),
                "anchorDate": anchor_date_str,
                "frequency": final_freq,
                "frequencyInterval": final_interval,
                "autoAdjustAmount": is_variable,
                "paused": False,
                "name": name,
                "reassignmentRule": reassignment_rule
            }
        }

        response = requests.post(URL, headers=headers, json={
            "operationName": "CreateBill",
            "variables": variables,
            "query": query_string
        })

        data = response.json()
        
        if 'errors' in data:
            return {"error": data['errors'][0]['message']}
            
        print("🧹 Clearing Cache after bill creation...")
        cache.clear()
        
        # --- 5. Fetch Funding Name & Combine ---
        result = data.get("data", {}).get("createBill", {}).get("result", {})
        
        # Fetch the name from the separate query you provided
        funding_name = get_bill_funding_source()
        
        # Inject it into the result for the frontend
        result['fundingDisplayName'] = funding_name
        
        return {"success": True, "result": result}

    except Exception as e:
        return {"error": str(e)}

# --- ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

# --- PWA ROUTES ---
@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/json')

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')

# --- API ROUTES ---
@app.route('/api/family')
def api_family(): return jsonify(get_family_data())
@app.route('/api/cards')
def api_cards(): return jsonify(get_cards_data())
@app.route('/api/savings')
def api_savings(): return jsonify(get_financial_data())
@app.route('/api/history')
def api_history(): return jsonify(get_history())
@app.route('/api/transactions')
def api_transactions():
    q = request.args.get('q')
    min_date = request.args.get('minDate')
    max_date = request.args.get('maxDate')
    min_amt = request.args.get('minAmt')
    max_amt = request.args.get('maxAmt')
    return jsonify(get_transactions_data(q, min_date, max_date, min_amt, max_amt))
@app.route('/api/transaction/<path:tx_id>')
def api_transaction_detail(tx_id): return jsonify(get_transaction_detail(tx_id))

@app.route('/api/expenses')
def api_expenses():
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_expenses_data(force_refresh=refresh))

@app.route('/api/goals')
def api_goals():
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_goals_data(force_refresh=refresh))

@app.route('/api/trends')
def api_trends(): return jsonify(get_monthly_trends())

@app.route('/api/subaccounts')
def api_subaccounts():
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_subaccounts_list(force_refresh=refresh))

@app.route('/api/move-money', methods=['POST'])
def api_move_money():
    data = request.json
    return jsonify(move_money(data.get('fromId'), data.get('toId'), data.get('amount'), data.get('note')))

@app.route('/api/delete-pocket', methods=['POST'])
def api_delete_pocket():
    data = request.json
    return jsonify(delete_subaccount_action(data.get('id')))


@app.route('/api/create-pocket', methods=['POST'])
def api_create_pocket():
    data = request.json
    return jsonify(create_pocket(
        data.get('name'), 
        data.get('amount'), 
        data.get('initial'), 
        data.get('note')
    ))

@app.route('/api/delete-bill', methods=['POST'])
def api_delete_bill():
    data = request.json
    return jsonify(delete_bill_action(data.get('id')))


@app.route('/api/create-bill', methods=['POST'])
def api_create_bill():
    data = request.json
    return jsonify(create_bill_action(
        data.get('name'),
        data.get('amount'),
        data.get('frequency'),
        data.get('dayOfMonth'),
        data.get('matchString'),
        data.get('minAmount'),
        data.get('maxAmount'),
        data.get('variable')
    ))

if __name__ == '__main__':
    init_db()
    print("Server running on http://127.0.0.1:8080")
    app.run(host='0.0.0.0', debug=True, port=8080)
