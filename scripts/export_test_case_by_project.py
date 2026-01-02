import requests
import json
import csv
import os
import re
from datetime import datetime

# Configuration Paths - Relative to the script location or absolute
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
ENV_FILE = os.path.join(PROJECT_ROOT, '.env')
SCHEMA_FILE = os.path.join(PROJECT_ROOT, 'data_files', 'schemas', 'search_tc_schema.json')
MAPPING_MAIN_FILE = os.path.join(PROJECT_ROOT, 'data_files', 'csv', 'search_tc_mapping_main_f.csv')
MAPPING_CUF_FILE = os.path.join(PROJECT_ROOT, 'data_files', 'csv', 'search_tc_mapping_cuf.csv')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'data_files', 'csv')

def load_env(env_path):
    """Manually load environment variables from a .env file."""
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars

def get_nested_value(data, path):
    """
    Retrieve value from nested dictionary using a path string like 'x.dataRows[0].data.id'.
    The path is expected to match the keys in the mapping files.
    We need to adapt the loose 'x.dataRows[0]' notation to the actual item dict.
    
    The mapping keys look like: x.dataRows[0].data.id
    The 'item' we are processing corresponds to one element of 'dataRows'.
    So 'x.dataRows[0]' effectively refers to 'item'.
    """
    # Remove the prefix that assumes we are looking at the whole response structure 
    # and focusing on a single row.
    # We'll treat 'x.dataRows[0].' as the root of our 'item'.
    
    clean_path = path.replace('x.dataRows[0].', '')
    
    # Handle array access in keys if any (though simple split might suffice for now)
    # The CUF keys look like: data["cuf|1"] which requires special handling
    
    try:
        current = data
        
        # Check for bracket notation specifically for CUF like data["cuf|1"]
        # Regex to find patterns like key["value"]
        # But a simpler approach for this specific case:
        
        # Split by dots, but respect quotes
        parts = []
        pattern = re.compile(r'\.|\["|"]|\[\'|\']')
        raw_parts = pattern.split(clean_path)
        parts = [p for p in raw_parts if p] # filter empty strings

        for part in parts:
            if isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    return ""
            elif isinstance(current, list):
                try:
                    idx = int(part)
                    if idx < len(current):
                        current = current[idx]
                    else:
                        return ""
                except ValueError:
                    return ""
            else:
                return ""
        return current
    except Exception as e:
        return ""

def login(base_url, username, password):
    session = requests.Session()
    
    # 1. Get XSRF Token (Handshake)
    login_page_url = f"{base_url}/login"
    print(f"Connecting to {login_page_url}...")
    try:
        response = session.get(login_page_url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error accessing login page: {e}")
        return None, None

    # Extract XSRF-TOKEN from cookies
    xsrf_token = session.cookies.get('XSRF-TOKEN')
    if not xsrf_token:
        print("Failed to obtain XSRF-TOKEN")
        return None, None
    print(f"XSRF-TOKEN obtained: {xsrf_token}")

    # 2. Perform Login
    login_api_url = f"{base_url}/backend/login"
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-XSRF-TOKEN': xsrf_token
    }
    payload = {
        'username': username,
        'password': password
    }
    
    print("Logging in...")
    try:
        response = session.post(login_api_url, headers=headers, data=payload)
        response.raise_for_status()
        print("Login successful.")
        return session, xsrf_token
    except requests.exceptions.RequestException as e:
        print(f"Login failed: {e}")
        return None, None

def main():
    # 1. Load Environment Variables
    env = load_env(ENV_FILE)
    base_url = env.get('SQUASHTM_URL') or env.get('SQUASH_URL') or env.get('BASE_URL')
    
    username = env.get('SQUASHTM_USERNAME') or env.get('SQUASH_USERNAME') or env.get('USERNAME')
    password = env.get('SQUASHTM_PASSWORD') or env.get('SQUASH_PASSWORD') or env.get('PASSWORD')

    if not all([base_url, username, password]):
        print("Error: Missing configuration in .env. Need BASE_URL, USERNAME, PASSWORD (or SQUASH_ prefix).")
        return

    # 2. Login
    session, _ = login(base_url, username, password)
    if not session:
        return
    
    # Refresh XSRF-TOKEN from cookies after login
    xsrf_token = session.cookies.get('XSRF-TOKEN')

    # 3. Read JSON Schema and Prepare Request
    print(f"Reading schema from {SCHEMA_FILE}")
    with open(SCHEMA_FILE, 'r') as f:
        request_payload = json.load(f)

    # Extract Project Name for filename
    project_scope = request_payload.get('scope', [])
    project_name = project_scope[0] if project_scope else "UnknownProject"
    
    # Update size if needed? The plan says "strictly follow JSON". 
    # But usually 'size': 1 is for testing. I will print a warning if size is small.
    if request_payload.get('size', 0) < 10:
        print(f"Warning: 'size' in schema is set to {request_payload.get('size')}. Result will be limited.")

    # 4. Fetch Test Cases
    search_url = f"{base_url}/backend/search/test-case"
    headers = {
        'Content-Type': 'application/json',
        'X-XSRF-TOKEN': xsrf_token,
        'Origin': base_url.replace('/squash', ''),
        'Referer': f"{base_url}/search/test-case"
    }
    
    print(f"Fetching test cases for project: {project_name}")
    try:
        response = session.post(search_url, headers=headers, json=request_payload)
        response.raise_for_status()
        result_data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching test cases: {e}")
        print(response.text)
        return

    data_rows = result_data.get('dataRows', [])
    print(f"Retrieved {len(data_rows)} test cases.")

    # 5. Prepare CSV Headers and Mapping
    print("Reading CSV mappings...")
    
    headers = []
    mapping_keys = []
    
    # Read Main Mapping
    with open(MAPPING_MAIN_FILE, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                key = row[0].strip()
                header_name = row[1].strip()
                headers.append(header_name)
                mapping_keys.append(key)
            elif len(row) == 1:
                # Fallback if only key is present
                key = row[0].strip()
                headers.append(key)
                mapping_keys.append(key)
    
    # Read CUF Mapping
    # "nama kolom ... ganti pakai nama baru di kolom 2 agar lebih informatif"
    with open(MAPPING_CUF_FILE, 'r') as f:
        reader = csv.reader(f) # It's a CSV with valid CSV format likely
        for row in reader:
            if len(row) >= 2:
                key = row[0]
                header_name = row[1]
                headers.append(header_name)
                mapping_keys.append(key)

    # 6. Write to CSV
    today_date = datetime.now().strftime("%Y%m%d")
    output_filename = f"exported_test_case_{project_name}_{today_date}.csv"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        
    print(f"Writing data to {output_path}...")
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        
        for item in data_rows:
            row_data = []
            for key in mapping_keys:
                # We need to reconstruct the path logic properly
                # If key is "x.dataRows[0].data.id", we want item['data']['id']
                # If key is "x.dataRows[0].data["cuf|1"]", we want item['data']['cuf|1']
                
                # A robust way to handle the "data['key']" or dot notation is needed
                val = get_nested_value(item, key)
                row_data.append(val)
            writer.writerow(row_data)

    print("Success! Export complete.")

if __name__ == "__main__":
    main()
