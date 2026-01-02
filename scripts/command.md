Scope Project hanya di folder : /Users/muhammadarifin/Documents/Code/squashtm

Buat script python baru yang berisi dari kombinasi step :
- convert ke python file 'scripts/sample/login_squash.sh' dengan membaca url, username, password dari file '.env'
- script get test case dengan sampel curl dari file 'data_files/curl_sample/search_tc_by_project.txt' menggunakan login dari script sebelumnya
- script get test case request body membaca dari file 'data_files/schemas/search_tc_schema.json'
- simpan response data get test case ke dalam file format csv, data yang disimpan sesuai dengan alamat json di file 'data_files/csv/search_tc_mapping_main_f.csv' dan 'data_files/csv/search_tc_mapping_cuf.csv'
- nama kolom di file csv sesuai dengan nama key nya, kecuali untuk 'data_files/csv/search_tc_mapping_cuf.csv' ganti pakai nama baru di kolom 2 agar lebih informatif
- simpan script dengan nama 'export_test_case_by_project.py' di folder 'scripts' 
- simpan response csv dengan nama 'exported_test_case_${PROJECTNAME}_${TODAYDATE}.csv di folder 'data_files/csv'
- ${PROJECTNAME} ambil value dari file 'data_files/schemas/search_tc_schema.json' key "scope"