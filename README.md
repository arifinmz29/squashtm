# SquashTM & Regression Automation CLI

Aplikasi CLI interaktif berbasis Python yang menggabungkan seluruh script automasi **SquashTM**, **Google Sheets**, dan **PostgreSQL Reporting Database** ke dalam satu pintu (`main.py`), namun tetap mempertahankan arsitektur modular dengan file terpisah di dalam folder `scripts/` agar mudah dipelihara (*maintainable*).

---

## Struktur Direktori

```text
squashtm/
├── main.py                                       # Entry point utama aplikasi CLI Interaktif
├── credential_tools.json                         # File kredensial aktif (DI-IGNORE oleh Git)
├── credential_tools.example.json                 # Template contoh struktur credential_tools.json
├── requirements.txt                              # Daftar dependensi library Python
├── README.md                                     # Dokumentasi setup dan panduan penggunaan
├── .gitignore                                    # Konfigurasi file yang diabaikan Git
├── scripts/
│   ├── core/
│   │   ├── config.py                             # Loader credential_tools.json & manajemen direktori data
│   │   ├── cli.py                                # Helper prompt interaktif, validasi input, & warna terminal
│   │   ├── squash_client.py                      # Autentikasi SquashTM & helper Campaign/Iteration
│   │   ├── gsheet_client.py                      # Autentikasi Google Sheets API (Service Account)
│   │   └── db_client.py                          # Koneksi PostgreSQL Reporting Database
│   ├── delete_from_testrun_by_testcaseid.py      # [Fitur 1] Hapus Test Case dari Test Run (Iteration)
│   ├── export_test_cases_by_folder.py            # [Fitur 2] Export Test Case per Folder ke Excel (.xlsx)
│   ├── manual_regression_preparation.py          # [Fitur 3] Query DB Reporting -> Google Sheet ({tag}_report)
│   ├── update_custom_field_test_case.py          # [Fitur 4] Update Custom Field Test Case dari Google Sheet
│   ├── update_status_test_run_sqtm_multi.py      # [Fitur 5] Mass-Update Status Test Run V1 (Single Status)
│   └── update_status_test_run_sqtm_multi_v2.py   # [Fitur 6] Mass-Update Status Test Run V2 (Dynamic Mapping)
└── data_files/
    ├── csv/                                      # Input/Output file CSV & laporan eksekusi
    ├── json/                                     # File JSON sementara (mis. folder_tree.json)
    ├── xls/                                      # Output file Excel (.xlsx) hasil export folder
    └── schemas/                                  # Schema JSON untuk pencarian test case
```

---

## 1. Persiapan & Instalasi (Setup)

### A. Prasyarat Sistem
- **Python 3.9+** telah terpasang.
- Disarankan menggunakan *virtual environment* (`venv`).

```bash
cd squashtm

# Opsional: Buat dan aktifkan virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install seluruh dependensi
pip install -r requirements.txt
```

### B. Setup Google Service Account (untuk akses Google Sheets)
Jika Anda belum memiliki kredensial Service Account sendiri, ikuti langkah-langkah berikut:

1. Buka [Google Cloud Console](https://console.cloud.google.com).
2. Buat project baru (atau gunakan project yang sudah ada).
3. Aktifkan 2 API berikut:
   - **Google Sheets API**
   - **Google Drive API**
   *(Caranya: menu kiri → **APIs & Services** → **Enable APIs & services** → cari nama API → klik **Enable**)*
4. Buat Service Account:
   - Menu kiri → **IAM & Admin** → **Service Accounts**.
   - Klik **Create Service Account**.
   - Isi nama service account, lalu klik **Create and Continue** → **Done**.
5. Download Credentials (JSON Key):
   - Klik email Service Account yang baru dibuat.
   - Masuk ke tab **Keys** → **Add Key** → **Create new key** → pilih **JSON** → klik **Create**.
   - File JSON akan otomatis ter-download ke komputer Anda.
   - Salin seluruh isi objek dari file JSON tersebut ke dalam blok `"google_service_account"` pada file `credential_tools.json` (lihat bagian C di bawah).
6. Berikan akses ke Google Sheet:
   - Salin alamat email Service Account (`client_email`, formatnya: `xxx@xxx.iam.gserviceaccount.com`).
   - Buka Google Spreadsheet yang akan digunakan.
   - Klik tombol **Share** → tempel email Service Account tersebut → beri akses sebagai **Editor**.

### C. Konfigurasi `credential_tools.json`
Seluruh kredensial (**SquashTM**, **Database PostgreSQL Reporting**, dan **Google Service Account**) digabungkan ke dalam satu file bernama `credential_tools.json` di root repository. File ini sudah terdaftar di `.gitignore` sehingga **tidak akan ter-upload ke repository Git**.

1. Salin file template `credential_tools.example.json` menjadi `credential_tools.json`:
   ```bash
   cp credential_tools.example.json credential_tools.json
   ```
2. Buka `credential_tools.json` dan isi sesuai kredensial Anda (tempelkan isi file JSON dari langkah **B.5** ke dalam bagian `"google_service_account"`):
   ```json
   {
     "squashtm": {
       "url": "SQUASHTM_URL",
       "username": "YOUR_SQUASHTM_USERNAME",
       "password": "YOUR_SQUASHTM_PASSWORD",
       "dimension_type_default": ""
     },
     "database": {
       "host": "HOST_DB_REPORTING",
       "port": 5432,
       "dbname": "reporting",
       "user": "YOUR_DB_USERNAME",
       "password": "YOUR_DB_PASSWORD"
     },
     "google_service_account": {
       "type": "service_account",
       "project_id": "YOUR_PROJECT_ID",
       "private_key_id": "YOUR_PRIVATE_KEY_ID",
       "private_key": "-----BEGIN PRIVATE KEY-----\nYOUR_PRIVATE_KEY_CONTENT\n-----END PRIVATE KEY-----\n",
       "client_email": "YOUR_SERVICE_ACCOUNT_EMAIL@YOUR_PROJECT_ID.iam.gserviceaccount.com",
       "client_id": "YOUR_CLIENT_ID",
       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
       "token_uri": "https://oauth2.googleapis.com/token",
       "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
       "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/YOUR_SERVICE_ACCOUNT_EMAIL%40YOUR_PROJECT_ID.iam.gserviceaccount.com",
       "universe_domain": "googleapis.com"
     }
   }
   ```
3. **Penting untuk Fitur Google Sheets**: Pastikan Google Spreadsheet yang Anda gunakan sudah dibagikan (*Share* sebagai **Editor**) ke alamat `client_email` Service Account Anda (contoh: `YOUR_SERVICE_ACCOUNT_EMAIL@YOUR_PROJECT_ID.iam.gserviceaccount.com`).

---

## 2. Cara Menjalankan Aplikasi

### Mode 1: Aplikasi CLI Interaktif Terpusat (Direkomendasikan)
Jalankan `main.py` dari root folder repository:

```bash
python3 main.py
```

Anda akan disajikan menu interaktif bernomor (`1` s/d `7`, atau `0` untuk keluar):
- Pilih nomor fitur yang diinginkan.
- Aplikasi akan menanyakan variabel/parameter yang diperlukan satu per satu.
- Setiap prompt memiliki **nilai default** di dalam tanda kurung siku `[...]`. Tekan **Enter** jika ingin langsung menggunakan nilai default tersebut.
- Sebelum mengeksekusi perubahan, aplikasi akan menampilkan **Konfirmasi Parameter**.
- Anda dapat memilih menu **`[7] Cek Credential & Test Koneksi`** kapan saja untuk memverifikasi apakah login SquashTM, koneksi PostgreSQL, dan autentikasi Google Sheets sudah berfungsi dengan baik.

### Mode 2: Menjalankan Modul Secara Terpisah (Interaktif maupun Argumen CLI)
Setiap file di dalam folder `scripts/` tetap dapat dijalankan secara mandiri:
- Jika dijalankan **tanpa argumen**, script akan otomatis membuka prompt interaktif khusus untuk fitur tersebut.
- Jika dijalankan **dengan argumen CLI (`--help`)**, script akan langsung berjalan menggunakan flag yang diberikan.

---

## 3. Panduan Detail Setiap Fitur

### Fitur 1: Delete Test Cases from Test Run (`scripts/delete_from_testrun_by_testcaseid.py`)
Menghapus Test Case dari Iteration Test Plan di SquashTM berdasarkan daftar Test Case ID.

- **Variabel Interaktif**:
  - **Target Mode**: Pilih berdasarkan `Campaign ID` (otomatis mencari semua Iteration di bawah Campaign tersebut) atau input `Iteration ID` manual.
  - **Sumber Test Case ID**: Baca dari file CSV (default: `data_files/csv/delete_testrun.csv`) atau ketik langsung daftar Test Case ID dipisahkan koma (mis. `113501, 113502`).
  - **Dry-Run Mode**: Jika `Ya`, hanya menampilkan simulasi item yang cocok tanpa benar-benar menghapus.
  - **Delay**: Jeda waktu antar request delete (default `0.15` detik).
- **Output Laporan**: `data_files/csv/delete_result_{timestamp}.csv`
- **Contoh Eksekusi via CLI Flags**:
  ```bash
  # Menggunakan Campaign ID dan file CSV
  python3 scripts/delete_from_testrun_by_testcaseid.py -c 42683 -f data_files/csv/delete_testrun.csv

  # Simulasi (Dry-Run) dengan input Test Case ID langsung
  python3 scripts/delete_from_testrun_by_testcaseid.py -c 42683 --tc 113501,113502 --dry-run
  ```

---

### Fitur 2: Export Test Cases by Folder (`scripts/export_test_cases_by_folder.py`)
Mengekspor seluruh Test Case dari folder tertentu di SquashTM ke file `.xls` per sub-folder lalu menggabungkannya menjadi satu file `.xlsx`.

- **Variabel Interaktif**:
  - **Library Name**: Pilihan `Genesis`, `Customer Apps`, `Website`, atau input manual.
  - **Folder Name**: Nama folder yang ingin diekspor (default: `New Regression Testing`).
- **Output File**: `data_files/xls/export_by_folder_{folder_name}_{timestamp}.xlsx`
- **Contoh Eksekusi via CLI Flags**:
  ```bash
  python3 scripts/export_test_cases_by_folder.py -l "Genesis" -f "New Regression Testing"
  ```

---

### Fitur 3: Manual Regression Preparation (`scripts/manual_regression_preparation.py`)
Melakukan query hasil eksekusi automasi terbaru dari PostgreSQL Reporting Database (sudah di-dedup per `tr_tc_test_id` menggunakan `ROW_NUMBER()`) lalu mengekspor seluruh kolomnya ke worksheet baru di Google Spreadsheet.

- **Variabel Interaktif**:
  - **Release Tag**: Tag rilis yang dicari (contoh: `2026-11-12`).
  - **Platforms**: Daftar platform dipisahkan koma (default: `FE-Playwright, FE-Robotframework, BE-Playwright, BE-Robotframework`).
  - **Spreadsheet Name**: Nama file Google Spreadsheet tujuan.
  - **Target Sheet Name**: Nama tab/worksheet tujuan (default: `{release_tag}_report`).
- **Contoh Eksekusi via CLI Flags**:
  ```bash
  python3 scripts/manual_regression_preparation.py \
    --release-tag 2026-11-12 \
    --platforms FE-Playwright FE-Robotframework BE-Playwright BE-Robotframework \
    --spreadsheet "Worksheet Regression - Q4 2026 - Robot Framework & Playwright"
  ```

---

### Fitur 4: Update Custom Field Test Case (`scripts/update_custom_field_test_case.py`)
Mengupdate nilai Custom Field pada Test Case di SquashTM secara massal berdasarkan data 2 kolom di Google Sheet.

- **Format Google Sheet**:
  - **Baris 1**: Header (otomatis dilewati).
  - **Kolom A**: `TEST_CASE_ID` (contoh: `115469`).
  - **Kolom B**: `VALUE` (nilai baru untuk custom field yang dipilih).
- **Variabel Interaktif**:
  - **Spreadsheet Name** & **Worksheet / Tab Name** (default: `updateField`).
  - **Target Custom Field Code**: Pilihan `automationStatus`, `automationPlatform`, `AssignAutomation`, `sectionRegression`, atau kode custom lainnya.
  - **Delay**: Jeda waktu antar request PATCH (default `0.3` detik).
- **Output Laporan**: `data_files/csv/report_update_{field_code}_{timestamp}.csv`
- **Contoh Eksekusi via CLI Flags**:
  ```bash
  python3 scripts/update_custom_field_test_case.py \
    --spreadsheet "Worksheet Regression - Q4 2026 - Robot Framework & Playwright" \
    --sheet-name "updateField" \
    --field-code "automationStatus"
  ```

---

### Fitur 5: Update Status Test Run Multi-Iteration V1 (`scripts/update_status_test_run_sqtm_multi.py`)
Melakukan mass-update status eksekusi Test Run di berbagai Iteration sekaligus menggunakan daftar Test Case ID di **Kolom A** Google Sheet ke **satu status target** yang sama (mis. `SUCCESS`).

- **Variabel Interaktif**:
  - **Penentuan Iteration**: Berdasarkan `Campaign ID` atau input manual daftar `Iteration ID`.
  - **Spreadsheet Name** & **Worksheet Name** (default: `Success Test IDs`).
  - **Target Execution Status**: `SUCCESS`, `FAILURE`, `UNTESTABLE`, atau `BLOCKED`.
  - **Batch Size**: Jumlah item per panggilan API mass-update (default: `20`).
- **Output Mapping CSV**: `data_files/csv/test_id_from_iterations_multi.csv`
- **Contoh Eksekusi via CLI Flags**:
  ```bash
  python3 scripts/update_status_test_run_sqtm_multi.py \
    --campaign-id 31558 \
    --sheet-name "Success Test IDs" \
    --status SUCCESS
  ```

---

### Fitur 6: Update Status Test Run Multi-Iteration V2 (`scripts/update_status_test_run_sqtm_multi_v2.py`)
Versi lanjutan untuk mass-update status Test Run lintas Iteration yang membaca kolom ID dan kolom Status secara dinamis dari Google Sheet, lalu memetakan nilainya menjadi `SUCCESS` atau `FAILURE`.

- **Aturan Pemetaan Status**:
  - `PASS`, `Pass AT Without Adjustment`, `Pass AT With Adjustment`, `Pass Manual Testing` $\rightarrow$ **`SUCCESS`**
  - Nilai lainnya $\rightarrow$ **`FAILURE`**
  - Baris dengan status kosong / `#N/A` / `N/A` / `-` akan dilewati secara default (`Skip Status Kosong = True`).
- **Variabel Interaktif**:
  - **Penentuan Iteration**: Berdasarkan `Campaign ID` atau input manual daftar `Iteration ID`.
  - **Spreadsheet Name** & **Worksheet Name** (mis. `Run_Manual_20261008` atau `2026-11-12_report`).
  - **Header Kolom Test Case ID** (default: `tc_test_id`).
  - **Header Kolom Status**: Pilihan `Status Rerun`, `tr_test_result`, atau custom.
  - **Skip Status Kosong**: `Ya` / `Tidak`.
- **Output Mapping CSV**: `data_files/csv/test_id_from_iterations_multi_v2.csv`
- **Contoh Eksekusi via CLI Flags**:
  ```bash
  python3 scripts/update_status_test_run_sqtm_multi_v2.py \
    --campaign-id 47022 \
    --sheet-name "Run_Manual_20261008" \
    --header-test-id "tc_test_id" \
    --header-status "Status Rerun"
  ```
