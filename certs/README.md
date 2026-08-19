# Russian Trusted CA files for MAX API

Source page: https://www.gosuslugi.ru/crt  
Official static host used by that page: https://gu-st.ru

Downloaded 2026-08-19 from:

- `https://gu-st.ru/content/lending/linux_russian_trusted_root_ca_pem.zip`
  - ZIP SHA-256: `ca99ca9b0022ec8b99d5822502cf3f38d4797bdd02cc098996778421d72d7e24`
- `https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.zip`
  - ZIP SHA-256: `35d8ce3ed079b1cd3a1650bf2ed2d873eee288799924dbbe128c172b65d3594e`

Files installed into the Docker trust store:

1. `russian_trusted_root_ca_pem.crt`
   - Subject: `C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Root CA`
   - Valid until: 2032-02-27
   - SHA-256 fingerprint: `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`
2. `russian_trusted_sub_ca_2024_pem.crt`
   - Subject: `C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Sub CA`
   - Valid until: 2029-07-19
   - SHA-256 fingerprint: `21:55:78:50:36:C9:00:DB:B5:F1:BB:2A:15:69:C8:0C:55:59:5B:D6:BF:94:86:7A:29:BB:DD:BC:7D:88:A3:F2`

`platform-api2.max.ru` presented an RSA leaf issued by the 2024 Russian Trusted Sub CA during verification on 2026-08-19. GOST and legacy 2022 sub-CA files were intentionally not installed because this endpoint did not require them.
