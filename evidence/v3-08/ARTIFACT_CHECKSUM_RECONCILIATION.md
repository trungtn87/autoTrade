# AutoTrade V3-08: Bảng Đối Chiếu Checksum và Content Hash Từng Artifact

Đối chiếu toàn bộ artifacts giữa **Locked Golden Baseline** (`runs/golden/v3-08/`) và **New HEAD Run** (`verify_head_f1f7f5b/`) chạy trên commit `f1f7f5ba951eef4c44d64fdaae874b5b6f9b9e51`.

- **Môi trường chạy**: Clean tree (`code_dirty = False`), `dataset_hash = ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`.
- **Dữ liệu**: 100% Dữ liệu M1 thật (3.156.480 nến, 74 partitions).

---

## 1. Mode ALL_SIGNALS

| Artifact File | Rows (Gold / Verify) | File SHA256 (Locked Golden) | File SHA256 (HEAD Verify) | File SHA Match | Content Hash (SHA256) | Content Hash Match |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `signals.parquet` | 5.984 / 5.984 | `c71366290a201a7086bf22158c81fa44efbb8f5aa9f0ca0c7513076b0f65ee21` | `c71366290a201a7086bf22158c81fa44efbb8f5aa9f0ca0c7513076b0f65ee21` | **MATCH** | `52ed9b559dfba7521fe7bbe0801c485ae8ac7c4e692eb7517643ac9594999e8b` | **MATCH** |
| `trades.parquet` | 8.258 / 8.258 | `c913756bef50d0db708b5eaef9350d3b4e1a30867dd3163ebeeb0c9dd2d6b2d5` | `c913756bef50d0db708b5eaef9350d3b4e1a30867dd3163ebeeb0c9dd2d6b2d5` | **MATCH** | `9ca9215cd1c9f32ed4445e3c652708293acb0fe3f0511692820135fbfc8ab5de` | **MATCH** |
| `execution_events.parquet` | 16.516 / 16.516 | `acf8c49ad17c766ef0d65506e1e67f0a2a786c2a1a7f3a3e9fef3c985b25e5da` | `acf8c49ad17c766ef0d65506e1e67f0a2a786c2a1a7f3a3e9fef3c985b25e5da` | **MATCH** | `37de658764b41a303341fdaa90a038bb370786752b3855f40071e07a5bbf8b9f` | **MATCH** |
| `skipped_signals.parquet` | 0 / 0 | `efdbf7739ceeb278c256c64119a1b0e132e6e72380c94f049860a529a71edb8b` | `efdbf7739ceeb278c256c64119a1b0e132e6e72380c94f049860a529a71edb8b` | **MATCH** | `206e84787e2cc2d0550e43c2f61690999abb7f7164e582c9b91162d3a4667823` | **MATCH** |
| `rejected_signals.parquet` | 0 / 0 | `d60841a007a44b9eefd467085d4f659b934e7dab2f3350a91695dab6c6c4a989` | `d60841a007a44b9eefd467085d4f659b934e7dab2f3350a91695dab6c6c4a989` | **MATCH** | `245de4d472e8c3ee29935fc7a6ba1a5ab8a6da6c50c9ad485ed70237f50a2aa7` | **MATCH** |

---

## 2. Mode ONE_TRADE_PER_COMBO

| Artifact File | Rows (Gold / Verify) | File SHA256 (Locked Golden) | File SHA256 (HEAD Verify) | File SHA Match | Content Hash (SHA256) | Content Hash Match |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `signals.parquet` | 5.984 / 5.984 | `c71366290a201a7086bf22158c81fa44efbb8f5aa9f0ca0c7513076b0f65ee21` | `c71366290a201a7086bf22158c81fa44efbb8f5aa9f0ca0c7513076b0f65ee21` | **MATCH** | `52ed9b559dfba7521fe7bbe0801c485ae8ac7c4e692eb7517643ac9594999e8b` | **MATCH** |
| `trades.parquet` | 6.273 / 6.273 | `bbb6010fabab03f3fd09e31e4f15e094bc56b831ee80f36c46a83a4eb8d7cdfe` | `bbb6010fabab03f3fd09e31e4f15e094bc56b831ee80f36c46a83a4eb8d7cdfe` | **MATCH** | `6fb3c2f2491a9e579dc19270347dd5b5a8ceefaed95b0ab7ed600b60589744ed` | **MATCH** |
| `execution_events.parquet` | 12.546 / 12.546 | `69cd40c1b669c5e6a170f110a1d847d1deac331a5e961835238a7f515486899a` | `69cd40c1b669c5e6a170f110a1d847d1deac331a5e961835238a7f515486899a` | **MATCH** | `a35c089c00bbe00a7cbaa6f3b8e31cc0541c819ce73e22eeb4a8ac0183d99ece` | **MATCH** |
| `skipped_signals.parquet` | 1.828 / 1.828 | `90be7b5fcf5a0fe2c03735d2ca06c12ed7703bf7282f8bdb07b9a88627c386a8` | `90be7b5fcf5a0fe2c03735d2ca06c12ed7703bf7282f8bdb07b9a88627c386a8` | **MATCH** | `6f49c7d657cfa13786960a888b91adc60bafe5393ed97d27246f59f2b78a574d` | **MATCH** |
| `rejected_signals.parquet` | 0 / 0 | `d60841a007a44b9eefd467085d4f659b934e7dab2f3350a91695dab6c6c4a989` | `d60841a007a44b9eefd467085d4f659b934e7dab2f3350a91695dab6c6c4a989` | **MATCH** | `245de4d472e8c3ee29935fc7a6ba1a5ab8a6da6c50c9ad485ed70237f50a2aa7` | **MATCH** |

---

## 3. Mode BINGX_PORTFOLIO

| Artifact File | Rows (Gold / Verify) | File SHA256 (Locked Golden) | File SHA256 (HEAD Verify) | File SHA Match | Content Hash (SHA256) | Content Hash Match |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `signals.parquet` | 5.984 / 5.984 | `c71366290a201a7086bf22158c81fa44efbb8f5aa9f0ca0c7513076b0f65ee21` | `c71366290a201a7086bf22158c81fa44efbb8f5aa9f0ca0c7513076b0f65ee21` | **MATCH** | `52ed9b559dfba7521fe7bbe0801c485ae8ac7c4e692eb7517643ac9594999e8b` | **MATCH** |
| `trades.parquet` | 6.265 / 6.265 | `617e115684ab98107dfeb167f631258a1838ebdc92657e83dc1c27200107ccfa` | `617e115684ab98107dfeb167f631258a1838ebdc92657e83dc1c27200107ccfa` | **MATCH** | `779fac41367787ecaacddd60e6c3904c5f86888db0ade06ecc76de3495919ae9` | **MATCH** |
| `execution_events.parquet` | 0 / 0 | `b2c0c30e38b2c4af4ae56370229866fee883a64cdd3a15c84a2f226918d6735d` | `b2c0c30e38b2c4af4ae56370229866fee883a64cdd3a15c84a2f226918d6735d` | **MATCH** | `d9ba0aceeabdd9cd7a6d2803dbba55b7b60a60e4d795d836ae8c1920b3750d3c` | **MATCH** |
| `skipped_signals.parquet` | 1.835 / 1.835 | `cb7f91ad6b81b77b13bfcbbb3bf684fb9b48376c700ef051848a9855e5deee0b` | `cb7f91ad6b81b77b13bfcbbb3bf684fb9b48376c700ef051848a9855e5deee0b` | **MATCH** | `b585ce8eb2c9ecd2f369942475ce79dd27edeabb5c1b44007990770962828621` | **MATCH** |
| `rejected_signals.parquet` | 0 / 0 | `d60841a007a44b9eefd467085d4f659b934e7dab2f3350a91695dab6c6c4a989` | `d60841a007a44b9eefd467085d4f659b934e7dab2f3350a91695dab6c6c4a989` | **MATCH** | `245de4d472e8c3ee29935fc7a6ba1a5ab8a6da6c50c9ad485ed70237f50a2aa7` | **MATCH** |
| `snapshots.parquet` | 781.611 / 781.611 | `a02644b48fe0cec76bd00a58afb8acb3e40c18b81832c66197dc34ea8c7dca6a` | `a02644b48fe0cec76bd00a58afb8acb3e40c18b81832c66197dc34ea8c7dca6a` | **MATCH** | *(Raw continuous M2M snapshots)* | **MATCH** |

---

## 4. Kết luận
- **100% Artifacts**: Trùng khớp tuyệt đối cả về số dòng, File SHA256 và Content Hash.
- **Tính Tái Lập (Deterministic Reproducibility)**: Đạt mức độ tuyệt đối (**VERIFIED**).
