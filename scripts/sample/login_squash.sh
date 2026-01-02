#!/bin/bash

# Konfigurasi
BASE_URL="https://synthesis.thelionparcel.com/squash"
COOKIE_FILE="cookies.txt"
USERNAME=""
# PERINGATAN: Hindari hardcode password di script production
PASSWORD="" 

# --- LANGKAH 1: Dapatkan Cookie awal (Handshake) ---
# Kita melakukan request ke halaman login hanya untuk mendapatkan XSRF-TOKEN & JSESSIONID
echo "1. Mengambil XSRF-TOKEN awal..."
curl -s -c $COOKIE_FILE -L "$BASE_URL/login" > /dev/null

# --- LANGKAH 2: Extract nilai XSRF-TOKEN dari file cookie ---
# Kita perlu membaca file cookies.txt dan mengambil value dari token XSRF-TOKEN
# Format cookie file biasanya: domain flag path secure expiry name value
TOKEN=$(grep "XSRF-TOKEN" $COOKIE_FILE | awk '{print $7}')

if [ -z "$TOKEN" ]; then
  echo "Gagal mendapatkan XSRF-TOKEN. Cek koneksi atau URL."
  exit 1
fi

echo "Token didapat: $TOKEN"

# --- LANGKAH 3: Lakukan Login ---
echo "2. Melakukan Login..."
curl -X POST "$BASE_URL/backend/login" \
  -b $COOKIE_FILE \
  -c $COOKIE_FILE \
  -H "accept: application/json, text/plain, */*" \
  -H "x-xsrf-token: $TOKEN" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$USERNAME" \
  --data-urlencode "password=$PASSWORD" 

echo -e "\nLogin selesai. Cookie tersimpan di $COOKIE_FILE"