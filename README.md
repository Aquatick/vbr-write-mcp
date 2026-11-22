# vbr-write-mcp

Veeam Backup & Replication (VBR) REST API'sinin **yazma ve yönetim** işlemlerini MCP tool'ları olarak sunan sunucu.
Veeam'in resmi [`vbr-mcp`](https://github.com/veeam-ai/veeam-mcp-server/tree/main/servers/vbr-mcp) sunucusunu
tamamlamak için yazıldı: vbr-mcp okuma ağırlıklıdır (49 tool'dan 29'u okuma), bu sunucu ise onun kapsamadığı yazma
işlemlerini ekler. İkisi yan yana çalışacak şekilde tasarlandı. Aynı `.env` değişkenlerini kullanırlar ve tool adları
çakışmaz.

| | vbr-mcp (Veeam) | vbr-write-mcp (bu proje) |
| --- | --- | --- |
| VBR 1.3-rev2 yazma endpoint'i (POST/PUT/DELETE, 336 adet) | ~20 | **Tamamı**: 68 tool'un ~150 endpoint'i doğrudan, geri kalanını `api_write` karşılar |
| Onay | Yalnızca istemciye ipucu (annotation) | **Sunucu tarafında zorunlu**: elicitation veya tek kullanımlık token |
| Dry-run / audit log / erişim seviyesi | Yok | Var |
| Düzenleme (PUT) | Yok | Patch mantığı: yalnızca değişen alanlar gönderilir, onay planında alan bazında diff gösterilir |
| İstek gövdesi şeması | Elle yazılmış modeller | Paketle gelen API kataloğu (519 endpoint, 1425 model): `describe_endpoint` / `describe_model` |

> **Test durumu:** 98 otomatik test ve uçtan uca demo testi geçiyor. Bunlar sahte (mock) bir VBR'a karşı çalışıyor ve gönderilen her isteğin
> gerçek bir 1.3-rev2 endpoint'i olduğunu, gövdedeki her alanın Veeam'in resmi modelinde bulunduğunu doğruluyor.
> **Canlı bir VBR 13'e karşı henüz denenmedi.** Önce lab ortamında `VBRW_DRY_RUN=true` ile deneyin.

## Tool'lar

**Seviye**: `read` hiçbir şeyi değiştirmez. `write` ekleme yapar ya da geri alınabilir bir değişiklik yapar;
varsayılan olarak onay istemez. `destructive` veri kaybına, kesintiye ya da korumanın durmasına yol açabilir; sunucu
çalıştırmadan önce onay ister. `mixed` tool'larda zararsız varyant `write`, tehlikeli varyant `destructive` kabul
edilir. Örneğin `set_protection_group_enabled` için enable `write`, disable `destructive`'tir.

### Çekirdek (her zaman açık)
| Tool | Seviye | Ne yapar |
| --- | --- | --- |
| `describe_endpoint` | read | API referansı: yol + method ile şema, ya da kelimeyle arama ("failover", "add repository") |
| `describe_model` | read | Bir modelin alanları, zorunlular, enum'lar ve polimorfik alt tipler (`x-subtypes`) |
| `api_read` | read | Herhangi bir GET isteği (id bulma, aksiyon sonrası kontrol) |
| `get_session_result` | read | Bir session'ın durumu ve sonucu. `wait_seconds` ile bitene kadar bekler, hata ve uyarıda log'ları da getirir |
| `api_write` | destructive | Kataloğdaki **herhangi bir** POST/PUT/DELETE isteği (Azure IR, Entra ID, HA cluster, KMS, Data Integration API...) |

### Nesne yönetimi: 4 tool ile ~60 endpoint
| Tool | Seviye | Ne yapar |
| --- | --- | --- |
| `create_object` | write | Nesne oluşturur: job, managed_server, repository, scale_out_repository, proxy, mount_server, credentials, cloud_credentials, encryption_password, kms_server, protection_group, recovery_token, unstructured_data_server, entra_id_tenant, ad_domain, global_vm_exclusion, role, user, datastore_latency |
| `update_object` | destructive | Yukarıdaki nesneleri **patch** mantığıyla düzenler: nesneyi okur, değişiklikleri derin birleştirir, tam modeli PUT eder. Planda diff görünür |
| `delete_object` | destructive | Siler. `deleteBackups` ve `uninstallEverything` seçenekleri planda ayrıca uyarı olarak gösterilir |
| `update_settings` | destructive | Sunucu ayarları (patch + diff): config_backup, general_options, email, notifications, event_forwarding, host_authentication, storage_latency, traffic_rules, malware_*, security_mfa, security_analyzer_schedule, repository_access_permissions, ... |

### Job'lar, session'lar, backup'lar
| Tool | Seviye | Ne yapar |
| --- | --- | --- |
| `clone_job` | write | Job'u klonlar |
| `start_quick_backup` | write | Quick Backup: vSphere, **Cloud Director**, Hyper-V ya da agent makinesinin anlık incremental'i |
| `apply_job_configuration` | write | Agent politikasının konfigürasyonunu agent'lara uygular |
| `clear_job_cache` | destructive | Agent politikasının backup cache'ini temizler |
| `stop_session` / `stop_task` / `stop_automation_session` | destructive | Restore, rescan, IR, import gibi herhangi bir session'ı ya da task'ı durdurur |
| `delete_backup` / `delete_backup_object` | destructive | Backup'ın tamamını ya da tek bir makinenin restore point'lerini siler. GFS dahil edilebilir, yalnızca konfigürasyondan kaldırma seçeneği de var |
| `move_backup` | destructive | Backup'ı başka bir repository'ye taşır |
| `copy_backups` | write | Backup'ı başka bir repository'ye ya da klasöre kopyalar |
| `move_backup_objects_to_job` | destructive | Makinelerin backup'larını başka bir job'a bağlar |
| `run_backup_health_check` | mixed | Sağlık kontrolü yapar. `repair=true` ise destructive sayılır |
| `set_backup_file_malware_state` | mixed | Backup dosyasını clean ya da infected olarak işaretler |
| `manage_move_copy_session` | mixed | Bekleyen move/copy session'ı için Retry, ForgetFailed ya da StopAndUndo |
| `create_recovery_media` | write | Agent backup'ı için bare-metal recovery ISO'su üretir |

### Restore ve DR
| Tool | Seviye | Ne yapar |
| --- | --- | --- |
| `restore_cloud_director_vm` | destructive | **vCD VM'inin tam restore'u**: orijinal konuma ya da başka bir vApp'e, yeni ad, network ve storage policy ile |
| `restore_hyperv_vm` | destructive | Hyper-V VM'inin tam restore'u |
| `start_instant_recovery_hyperv_vm` / `finish_instant_recovery_hyperv_vm` | destructive | Hyper-V Instant Recovery (OtherPlatform dahil), ardından migrate ya da unmount |
| `start_file_level_restore` → `browse_file_level_restore` → `restore_files` → `unmount_file_level_restore` | write / read / mixed / write | Guest dosya restore akışı: mount, gez ya da ara, geri yükle (Keep, Overwrite ya da PermissionOnly; orijinal ya da başka konum), unmount |
| `list_replicas` | read | Replica'lar ve restore point'leri |
| `failover_replicas` | destructive | Failover: now, planned, permanent ya da undo |
| `failback_replicas` | destructive | Failback: start, commit, undo, switch_to_production ya da change_switchover_time |
| `rescan_replicas` | write | Replica'ları yeniden tarar |

### Agent, malware, lisans, güvenlik, altyapı, sistem
| Tool | Seviye | Ne yapar |
| --- | --- | --- |
| `set_protection_group_enabled`, `rescan_protection_group` | mixed / write | Protection group'u açar, kapatır ya da tarar |
| `manage_discovered_computers` | mixed | Agent ya da CBT kur, yükselt ya da kaldır; rescan, trust, **reboot**, gruptan çıkarma |
| `set_agent_backup_attachment`, `reboot_recovery_appliances` | mixed / write | Backup'ı agent'a bağlar ya da ayırır; recovery appliance'ı yeniden başlatır |
| `mark_backup_objects_clean`, `manage_malware_exclusions`, `set_ioc_monitoring` | destructive / mixed | False positive temizliği, istisnalar, IoC izleme |
| `start_encryption_analysis`, `report_malware_event` | write | Şifreleme analizi; EDR, XDR ya da SIEM'den malware olayı bildirme |
| `install_license`, `refresh_license`, `set_license_options`, `manage_instance_license`, `remove_installed_license` | karışık | Lisans yaşam döngüsü |
| `change_credentials_secret`, `change_encryption_password`, `verify_encryption_password` | destructive / read | Parola, SSH anahtarı ve cloud secret rotasyonu |
| `manage_user_security`, `clone_role`, `security_analyzer` | destructive / write / mixed | MFA reset, service account, rol klonlama, Security & Compliance Analyzer |
| `rescan_managed_servers`, `rescan_inventory`, `set_default_mount_server` | write | Altyapı rescan'leri, varsayılan mount server |
| `get_server_certificate`, `discover_cloud_director_vcenters` | read | Sunucu eklemeden önce TLS ya da SSH parmak izi; vCD'ye bağlı vCenter'lar |
| `export_configuration`, `import_configuration` | mixed / destructive | Job, sunucu, repo, proxy, credentials ve şifre export/import (sunucu taşıma, dokümantasyon) |
| `export_support_logs`, `send_test_email`, `high_availability_cluster` | write / write / destructive | Destek log paketi, test e-postası, HA switchover ve failover |

## Güvenlik modeli

1. **Sunucu tarafında onay (`VBRW_CONFIRMATION_MODE`, varsayılan `auto`)**
   - `elicit`: Sunucu, MCP istemcisi üzerinden **kullanıcıya doğrudan sorar**. Ajan kullanıcı yerine "evet"
     diyemez. Hem eski (2025-xx) hem yeni (2026-07-28, çok turlu "input required") protokolde çalışır.
   - `token`: İlk çağrı hiçbir şey göndermez. Geriye bir **plan** (hedef, gönderilecek istekler, etkisi, diff) ve
     5 dakika geçerli, **tek kullanımlık** bir `confirm_token` döner. Token aynı tool'a ve aynı argümanlara
     bağlıdır. Düzenleme işlemlerinde nesnenin o anki haline de bağlıdır: plan çıkarıldıktan sonra biri nesneyi
     değiştirirse token geçersiz olur.
   - `auto`: İstemci elicitation destekliyorsa elicit, desteklemiyorsa token kullanılır. `none` onayı kapatır;
     önerilmez.
2. **Erişim seviyesi (`VBRW_ACCESS_LEVEL`)**: `write` ayarında destructive tool'lar listelenmez, mixed tool'ların
   tehlikeli varyantları da reddedilir. **Production için önerilen ayar budur.**
3. **Dry-run (`VBRW_DRY_RUN=true`)**: Hiçbir yazma isteği gönderilmez, gönderilecek istek gösterilir.
4. **Audit log (`VBRW_AUDIT_LOG`)**: Her deneme JSONL olarak kaydedilir: plan, onay türü, sonuç, hata, istemci.
   Parola, anahtar, secret ve lisans gibi alanlar hem planda hem log'da `***` olarak maskelenir.
5. **Kapsam daraltma**: `VBRW_TOOL_GROUPS`, `VBRW_DISABLED_TOOLS`, `VBRW_BLOCKED_PATHS` (`api_write` için ek
   yasaklar). `/api/oauth2/*` ve `registerVbr` her zaman yasaktır.
6. **En az yetki**: Sunucu, `VBR_USERNAME` hesabının tüm yetkileriyle çalışır. Ayrı bir servis hesabı açın ve
   rolünü ihtiyaca göre seçin (ör. yalnızca restore yapacaksanız *Restore Operator*).

## Kurulum

```bash
cd vbr-write-mcp
cp .env.example .env        # VBR_BASE_URL, VBR_USERNAME, VBR_PASSWORD
uv sync
npx @modelcontextprotocol/inspector uv run vbr-write-mcp   # istemcisiz deneme
```

Docker ile:

```bash
docker build -f docker/Dockerfile -t vbr-write-mcp .
docker run -i --rm --env-file /abs/yol/vbr-write-mcp/.env -v vbrw-audit:/audit vbr-write-mcp
```

Gereksinimler: VBR 13 (REST API 1.3-rev2, port 9419), Python 3.12+ ya da uv/Docker.

### Claude Desktop: vbr-mcp ile yan yana

```json
{
  "mcpServers": {
    "vbr": {
      "command": "uv",
      "args": ["run", "--project", "/abs/yol/veeam-mcp-server/servers/vbr-mcp", "vbr-mcp"]
    },
    "vbr-write": {
      "command": "uv",
      "args": ["run", "--project", "/abs/yol/vbr-write-mcp", "vbr-write-mcp"],
      "env": { "VBRW_ACCESS_LEVEL": "write" }
    }
  }
}
```

### Claude Code

```bash
claude mcp add vbr-write -- uv run --project /abs/yol/vbr-write-mcp vbr-write-mcp
```

### HTTP (paylaşımlı kullanım)

`VBRW_TRANSPORT=http`, `VBRW_HTTP_PORT=8001`, `MCP_TOKENS={"<uzun-rastgele-token>": "ekip"}`. İstemciler
`Authorization: Bearer <token>` ile bağlanır. Sunucuyu yalnızca güvenilir bir ağda ya da bir reverse proxy
arkasında açın.

## Örnek akışlar

**vCD VM'ini yeni bir vApp'e restore etmek**
1. vbr-mcp: `list_protected_objects` → `list_restore_points` ile restore point id'sini bulun.
   `browse_inventory` ile hedef vApp nesnesini alın.
2. `restore_cloud_director_vm(restore_point_id, mode="Customized", new_name="web-01-restored", vapp={...})`
3. Planı onaylayın, ardından `get_session_result(session_id, wait_seconds=600)` ile takip edin.

**Job retention'ını değiştirmek**: `update_object(kind="job", id=..., changes={"storage": {"retentionPolicy":
{"quantity": 14}}})` çağrısı planda `storage.retentionPolicy.quantity: 7 → 14` diff'ini gösterir.

**Planlı DR testi**: `list_replicas` → `failover_replicas(mode="planned", ...)` → test →
`failback_replicas(action="start", target="OriginalVM", switchover={"type": "Manual"})` →
`failback_replicas(action="switch_to_production")` → `failback_replicas(action="commit")`.

**Dedicated tool'u olmayan bir işlem**: `describe_endpoint("instant recovery azure")` ile endpoint'i bulun,
`describe_endpoint("/api/v1/restore/instantRecovery/azure/vm", "POST")` ile gövdeyi görün, ardından
`api_write("POST", "/api/v1/restore/instantRecovery/azure/vm", body={...})` çağırın.

## Demo: gerçek VBR olmadan deneme

`demo/demo_vbr.py`, Veeam'in vbr-mcp entegrasyon testlerindeki VBR standını (Daily-VMs, SQL-Prod, SQL-Dev, Repo-Main,
Repo-Linux-01, malware olayları, lisans ...) sunan sahte bir VBR'dır. VBR 1.3-rev2'nin her yazma endpoint'ini kabul
eder, kaydeder ve VBR gibi cevap verir: aksiyon session döner, PUT nesneyi değiştirir, DELETE siler.

```bash
uv sync --all-groups
uv run python demo/demo_vbr.py            # http://127.0.0.1:9419 - giriş demo/demo
# başka bir terminalde: MCP Inspector ile tool'ları elle çağırın
VBR_BASE_URL=http://127.0.0.1:9419 VBR_USERNAME=demo VBR_PASSWORD=demo VBRW_CONFIRMATION_MODE=token \
  npx @modelcontextprotocol/inspector uv run vbr-write-mcp
```

Windows'ta: zip'i açın ve `start-demo.cmd` dosyasına çift tıklayın. Betik `.venv` kurar, demo VBR'ı başlatır, `/_demo`
sayfasını ve MCP Inspector'ı açar (Python 3.12+ ve Node.js gerekir).

`http://127.0.0.1:9419/_demo` sayfası VBR'a ulaşan her yazma isteğini canlı gösterir. `scripts/e2e_demo.py`, Veeam'in
vbr-mcp'si ile bu sunucuyu yan yana bu demo VBR'a bağlayıp senaryoları ve tüm tool'ları gerçek stdio + HTTP üzerinden
çalıştırır.

## Sınırlamalar

- Sunucu tek bir VBR'a bağlanır. Birden fazla VBR için her biri kendi `env`'iyle ayrı istemci girdisi ekleyin.
- vSphere replica failover ve failback API'si yalnızca **snapshot replica** içindir. CDP ve diğer platformlar
  VBR REST API'sinde yok.
- `x-subtypes` (polimorfik model alt tipleri), Veeam'in OpenAPI'sinden üretilmiş istemci modellerinden sezgisel
  olarak çıkarılıyor. Liste eksik ya da fazla olabilir. Şüphede kalırsanız `describe_model` ile alt tipe bakın.
- `copy_backups(kind="machines")` gövdesinin şeması Veeam tarafından yayımlanmıyor.
- `ACL`, `diskManagement` (bare-metal disk düzeni), Entra ID item restore ve Azure IR gibi nadir akışlar yalnızca
  `api_write` ile yapılabiliyor.

## Geliştirme

```bash
uv sync --all-groups
uv run --group test pytest -q tests                      # 98 test, VBR gerektirmez
uv run python scripts/stdio_legacy_check.py .venv/bin/vbr-write-mcp   # eski protokol elicitation e2e
```

- `tests/test_endpoints.py`: Her yazma tool'unu MCP protokolü üzerinden çalıştırır. Gönderilen her isteğin gerçek
  bir 1.3-rev2 endpoint'i olduğunu ve gövdedeki her alanın Veeam modelinde bulunduğunu doğrular. Yeni bir tool
  eklenip test durumu yazılmazsa test kırılır.
- `tests/test_safety.py`: Onay token'ı (tek kullanım, argümana ve nesne durumuna bağlılık), elicitation (kabul ve
  ret), dry-run, erişim seviyesi, redaction, audit, diff ve VBR hata mesajlarının iletilmesi.
- API kataloğunu yeni bir revizyona güncellemek için: `scripts/build_catalog.py`.

Lisans: MIT. `vbr_client.py`, `auth.py` ve `pagination.py` Veeam'in vbr-mcp kodundan alınmıştır (MIT,
`LICENSE.vbr-mcp`).
