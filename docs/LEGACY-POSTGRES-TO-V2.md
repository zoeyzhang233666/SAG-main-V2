# SAG / ChemClaw V1 PostgreSQL 备份迁移到 V2（Windows PowerShell 5.1）

> 本文档与压缩包中的脚本配套，已经按实际迁移流程修正。请保留脚本原路径与文件名：
>
> ```text
> scripts/export-legacy-for-v2.ps1
> scripts/import-legacy-to-v2.ps1
> ```

## 迁移内容与限制

迁移程序只读取隔离 PostgreSQL 容器，不会修改旧数据库。

可迁移内容：

- 文档；
- 文档切片；
- 事件；
- 实体；
- 事件—实体关系；
- 旧 UUID、来源信息和原始元数据。

不会直接迁移的内容：

- 旧 pgvector 向量和 PostgreSQL `tsvector`；
- 旧实体类型的权重、阈值等运行配置；
- MCP 会话、消息和工具调用；
- 旧 AI Provider 设置中的 API Key。

旧向量会被排除，V2 可使用当前 Embedding 配置在 LanceDB 中重新建立向量索引。辅助数据会保存在 `archive-not-imported` 目录中，API Key 会脱敏。

---

## 一、检查旧备份

在 V2 项目根目录执行：

```powershell
$backup = "C:\Users\EDY\Nutstore\1\我的坚果云\芯化和云\2026年6月\SAG-main\backups\sag_lite.dump"

powershell -ExecutionPolicy Bypass `
  -File .\scripts\inspect-legacy-backup.ps1 `
  -BackupFile $backup
```

出现表、`TABLE DATA`、索引和外键清单，且没有 `pg_restore: error`，表示备份可读。

---

## 二、恢复到隔离 PostgreSQL 容器

下面的容器不开放宿主机端口，不会覆盖 V2 的 SQLite/LanceDB 数据：

```powershell
$suffix = Get-Date -Format "yyyyMMddHHmmss"
$container = "sag-legacy-pg-$suffix"
$volume = "sag_legacy_pgdata_$suffix"
$pgpass = "SAGLegacy2026Temp"

$volume | Set-Content -Encoding ascii ".\backups\legacy-volume-name.txt"
$container | Set-Content -Encoding ascii ".\backups\legacy-container-name.txt"

docker volume create $volume

docker run -d `
  --name $container `
  -e POSTGRES_USER=sag_lite `
  -e POSTGRES_PASSWORD=$pgpass `
  -e POSTGRES_DB=sag_lite `
  -v "${volume}:/var/lib/postgresql/data" `
  pgvector/pgvector:pg16

do {
  Start-Sleep -Seconds 2
  docker exec $container pg_isready -U sag_lite -d sag_lite
} until ($LASTEXITCODE -eq 0)

docker cp $backup "${container}:/tmp/sag_lite.dump"

docker exec `
  -e "PGPASSWORD=$pgpass" `
  $container `
  pg_restore `
  --exit-on-error `
  --verbose `
  --no-owner `
  --no-privileges `
  -U sag_lite `
  -d sag_lite `
  /tmp/sag_lite.dump
```

恢复完成后应回到 PowerShell 提示符，且没有 `pg_restore: error`。

可检查核心数量：

```powershell
$sql = @"
SELECT
  (SELECT count(*) FROM sources) AS sources,
  (SELECT count(*) FROM documents) AS documents,
  (SELECT count(*) FROM document_sections) AS sections,
  (SELECT count(*) FROM source_chunks) AS chunks,
  (SELECT count(*) FROM events) AS events,
  (SELECT count(*) FROM entities) AS entities,
  (SELECT count(*) FROM event_entities) AS relations;
"@

docker exec `
  -e "PGPASSWORD=$pgpass" `
  $container `
  psql -U sag_lite -d sag_lite -P pager=off -c $sql
```

本次旧库的预期数量：

```text
文档       36
切片       41,314
事件       41,314
实体       112,949
关系       212,448
```

---

## 三、导出为 V2 结构化 JSON

压缩包中的导出脚本已经使用 UTF-8 BOM 保存，兼容 Windows PowerShell 5.1。

```powershell
$container = "sag-legacy-pg-20260716104541"
$pgpass = "SAGLegacy2026Temp"

powershell -ExecutionPolicy Bypass `
  -File .\scripts\export-legacy-for-v2.ps1 `
  -Container $container `
  -PgPassword $pgpass
```

默认输出目录：

```text
.\backups\legacy-v2-export-时间戳\
├─ import\                 # V2 可导入 JSON，一份旧文档对应一个文件
├─ archive-not-imported\   # 辅助归档数据
├─ manifest.csv            # 每个文件的切片、事件、关系数量
└─ README-下一步.txt
```

默认排除 `deleted_at` 不为空的旧事件。确实需要恢复这些已删除事件时，增加：

```powershell
-IncludeDeletedEvents
```

导出完成后不要再次重复导出到同一个目录。每次导出默认创建新的时间戳目录。

---

## 四、启动 V2 并确认 API

```powershell
docker compose up -d --build
docker compose ps
```

自动获取 API 地址。请把 `if` 和 `else` 作为一个完整语句一次执行：

```powershell
$apiEndpoint = (docker compose port api 8000).Trim()

if ($apiEndpoint -match ':(\d+)$') {
  $apiBase = "http://localhost:$($Matches[1])"
} else {
  throw "无法识别 API 地址：$apiEndpoint"
}

$apiBase
Invoke-RestMethod "$apiBase/api/v1/system/ready"
```

正常结果一般为：

```text
http://localhost:8100
status = ready
db     = True
```

Web 页面通常为：

```text
http://localhost:3000
```

---

## 五、配置 Embedding

正式导入前，在 Web 页面进入“设置 → 模型与检索”，配置：

- Embedding Base URL；
- Embedding API Key；
- Embedding 模型；
- 正确的向量维度。

未配置 Embedding 时，文档和图谱仍可导入，但不会正常重建向量检索数据。

---

## 六、登录并创建目标信源

### 1. 登录

Windows PowerShell 5.1 中使用 UTF-8 字节发送 JSON：

```powershell
$loginJson = @{ name = "EDY" } | ConvertTo-Json

$auth = Invoke-RestMethod `
  -Method Post `
  -Uri "$apiBase/api/v1/auth/login" `
  -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($loginJson))

$token = [string]$auth.access_token
$headers = @{ Authorization = "Bearer $token" }
```

### 2. 新建一个空信源

```powershell
$sourceJson = @{
  name           = "旧版 SAG 知识库迁移"
  description    = "从旧 PostgreSQL 备份迁移"
  source_type    = "document"
  connector_kind = "file_upload"
} | ConvertTo-Json

$target = Invoke-RestMethod `
  -Method Post `
  -Uri "$apiBase/api/v1/sources" `
  -Headers $headers `
  -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($sourceJson))

# 强制转为单个字符串，避免把多个信源 ID 拼进 URL。
$sourceId = [string]$target.id

$sourceId
$sourceId.GetType().FullName
@($sourceId).Count
```

最后三项应显示：

```text
一个 32 位信源 ID
System.String
1
```

Windows PowerShell 5.1 有时会把 API 响应中的中文显示成 `æ...`，这通常只是终端解码显示问题。以浏览器中的名称为准，数据导入不受影响。

已有空信源时，也可以直接明确指定：

```powershell
$sourceId = "你的单个信源ID"
```

不要从多个空信源中直接取 `.id` 后不检查数量。

---

## 七、先拆分一个测试文件

不要直接把 36 个文件全部导入。先测试最小文件：

```powershell
$exportRoot = "D:\SAG升级ChemClaw\SAG-main-V2\backups\legacy-v2-export-时间戳"
$inputDir   = Join-Path $exportRoot "import"
$testRoot   = Join-Path $exportRoot "_test"
$testDir    = Join-Path $testRoot "import"
$restRoot   = Join-Path $exportRoot "_remaining"
$restDir    = Join-Path $restRoot "import"

if (-not (Test-Path -LiteralPath $inputDir -PathType Container)) {
  throw "原始导入目录不存在：$inputDir"
}

$allFiles = @(
  Get-ChildItem -LiteralPath $inputDir -Filter "*.json" -File
)

if ($allFiles.Count -eq 0) {
  throw "原始导入目录中没有 JSON 文件：$inputDir"
}

Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $restRoot -Recurse -Force -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Path $testDir -Force | Out-Null
New-Item -ItemType Directory -Path $restDir -Force | Out-Null

$testFile = $allFiles |
  Sort-Object Length |
  Select-Object -First 1

Copy-Item -LiteralPath $testFile.FullName -Destination $testDir -Force

foreach ($file in $allFiles) {
  if ($file.FullName -ne $testFile.FullName) {
    Copy-Item -LiteralPath $file.FullName -Destination $restDir -Force
  }
}

$testCount = @(Get-ChildItem -LiteralPath $testDir -Filter "*.json" -File).Count
$restCount = @(Get-ChildItem -LiteralPath $restDir -Filter "*.json" -File).Count

Write-Host "原始文件：$($allFiles.Count)"
Write-Host "测试文件：$($testFile.Name)"
Write-Host "测试目录文件数：$testCount"
Write-Host "剩余目录文件数：$restCount"
```

预期：

```text
原始文件：36
测试目录文件数：1
剩余目录文件数：35
```

---

## 八、测试导入一个文件

批量导入脚本已经修复 Windows PowerShell 5.1 的 `curl.exe` UTF-8 响应问题：响应会先写入临时文件，再按 UTF-8 解码，不需要再手工修改脚本。

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\import-legacy-to-v2.ps1 `
  -SourceId $sourceId `
  -Token $token `
  -InputDirectory $testDir `
  -ApiBase $apiBase
```

测试文件导入完成后检查：

```powershell
$check = Invoke-RestMethod `
  -Uri "$apiBase/api/v1/sources/$sourceId" `
  -Headers $headers

$check | Format-List document_count, chunk_count, event_count
```

本次最小测试文件《原油期货100问2026版》的预期值：

```text
document_count : 1
chunk_count    : 154
event_count    : 154
```

刷新浏览器并确认：

1. 文档能够打开；
2. 中文没有乱码；
3. 切片和事件数量正确；
4. 图谱中存在实体和关系；
5. 搜索功能正常。

---

## 九、导入剩余 35 个文件

测试成功后，只导入 `_remaining\import`：

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\import-legacy-to-v2.ps1 `
  -SourceId $sourceId `
  -Token $token `
  -InputDirectory $restDir `
  -ApiBase $apiBase
```

首次迁移建议先不要使用 `-WaitForVectorJobs`，让文档和图谱优先入库，向量任务在后台处理。

需要逐文件等待向量任务完成时，再追加：

```powershell
-WaitForVectorJobs
```

**不要在测试成功后再导入原始 `$inputDir` 整个目录。** 否则测试文件会被重复导入。

---

## 十、最终核对

```powershell
$final = Invoke-RestMethod `
  -Uri "$apiBase/api/v1/sources/$sourceId" `
  -Headers $headers

$final | Format-List id, document_count, chunk_count, event_count
```

预期核心数量：

```text
document_count = 36
chunk_count    = 41314
event_count    = 41314
```

实体最终数量可能略有变化，因为 V2 会按“实体类型 + 规范化名称”去重。关系数应接近旧库的 212,448。

---

## 十一、迁移完成前保留旧容器和卷

在确认以下项目全部正常前，不要删除旧容器和卷：

- 36 个文档均已导入；
- 切片、事件数量核对正确；
- 图谱实体和关系可用；
- 中文内容正常；
- 搜索及向量检索正常；
- 已生成并验证 V2 新版备份。

确认完成后才可清理：

```powershell
docker rm -f $container
docker volume rm $volume
```

---

## 十二、常见错误

### `else` 无法识别

原因：在交互式 PowerShell 中，`if {}` 已经执行结束，再单独输入 `else`。解决方法是把 `if ... else ...` 整段一次执行。

### `导入目录不存在`

原因：只给 `$testDir` 赋值，没有创建目录和复制测试文件。先执行“七、先拆分一个测试文件”。

### `$sourceId` 显示两个 ID

原因：变量中保存了多个对象的 `.id`。直接明确指定一个 ID，或使用新建信源返回值：

```powershell
$sourceId = [string]$target.id
```

并确认：

```powershell
@($sourceId).Count
```

结果必须是 `1`。

### `API 返回的不是有效 JSON`

当前压缩包中的 `scripts/import-legacy-to-v2.ps1` 已修复此问题。不要用旧版脚本覆盖它。修复版会把 `curl.exe` 响应写入临时文件后按 UTF-8 读取。

### 脚本出现乱码和大量 ParserError

当前压缩包中的两个迁移脚本已保存为 UTF-8 BOM。可验证导出脚本前三字节：

```powershell
$bytes = [IO.File]::ReadAllBytes(
  (Resolve-Path ".\scripts\export-legacy-for-v2.ps1").Path
)

"{0:X2} {1:X2} {2:X2}" -f $bytes[0], $bytes[1], $bytes[2]
```

应显示：

```text
EF BB BF
```
