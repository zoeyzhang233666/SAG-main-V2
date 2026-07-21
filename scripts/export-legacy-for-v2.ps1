[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Container,

    [Parameter(Mandatory = $true)]
    [string]$PgPassword,

    [string]$PgUser = "sag_lite",
    [string]$PgDatabase = "sag_lite",
    [string]$OutputDirectory = "",

    [switch]$IncludeDeletedEvents,
    [switch]$ExcludeArchivedDocuments,
    [switch]$SkipAuxiliaryArchive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$previousConsoleEncoding = [Console]::OutputEncoding
$previousOutputEncoding = $OutputEncoding
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path (Get-Location) ("backups\legacy-v2-export-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$importDirectory = Join-Path $OutputDirectory "import"
$archiveDirectory = Join-Path $OutputDirectory "archive-not-imported"
New-Item -ItemType Directory -Path $importDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $archiveDirectory -Force | Out-Null

$documentPredicate = if ($ExcludeArchivedDocuments) { "d.archived_at IS NULL" } else { "TRUE" }
$eventPredicate = if ($IncludeDeletedEvents) { "TRUE" } else { "e.deleted_at IS NULL" }

function Get-DockerPsqlArgs {
    param([Parameter(Mandatory = $true)][string]$Sql)

    return @(
        "exec",
        "-e", "PGPASSWORD=$PgPassword",
        "-e", "PGCLIENTENCODING=UTF8",
        $Container,
        "psql",
        "-X",
        "-v", "ON_ERROR_STOP=1",
        "-U", $PgUser,
        "-d", $PgDatabase,
        "-P", "pager=off",
        "-t",
        "-A",
        "-c", $Sql
    )
}

function Invoke-PsqlText {
    param([Parameter(Mandatory = $true)][string]$Sql)

    $dockerArgs = Get-DockerPsqlArgs -Sql $Sql
    $output = @(& docker @dockerArgs)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "psql 执行失败，退出码：$exitCode"
    }
    return (($output -join "`n").Trim())
}

function Invoke-PsqlInt64 {
    param([Parameter(Mandatory = $true)][string]$Sql)

    $text = Invoke-PsqlText -Sql $Sql
    $value = 0L
    if (-not [Int64]::TryParse($text, [ref]$value)) {
        throw "预期获得整数，但数据库返回：$text"
    }
    return $value
}

function Write-PsqlLines {
    param(
        [Parameter(Mandatory = $true)][string]$Sql,
        [Parameter(Mandatory = $true)][System.IO.StreamWriter]$Writer,
        [switch]$AsJsonArrayItems
    )

    $first = $true
    $dockerArgs = Get-DockerPsqlArgs -Sql $Sql
    & docker @dockerArgs | ForEach-Object {
        $line = [string]$_
        if ([string]::IsNullOrWhiteSpace($line)) {
            return
        }
        if ($AsJsonArrayItems -and -not $first) {
            $Writer.Write(",")
        }
        $Writer.Write($line)
        if (-not $AsJsonArrayItems) {
            $Writer.WriteLine()
        }
        $first = $false
    }
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "流式 psql 导出失败，退出码：$exitCode"
    }
    return (-not $first)
}

function Get-SafeFileName {
    param(
        [Parameter(Mandatory = $true)][int]$Index,
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$Id
    )

    $safe = $Title.Trim()
    foreach ($char in [IO.Path]::GetInvalidFileNameChars()) {
        $safe = $safe.Replace([string]$char, "_")
    }
    $safe = [regex]::Replace($safe, "\s+", " ").Trim(" ", ".")
    if ([string]::IsNullOrWhiteSpace($safe)) {
        $safe = "document"
    }
    if ($safe.Length -gt 80) {
        $safe = $safe.Substring(0, 80).Trim()
    }
    $shortId = if ($Id.Length -gt 8) { $Id.Substring(0, 8) } else { $Id }
    return ("{0:D3}-{1}-{2}.json" -f $Index, $safe, $shortId)
}

function Export-JsonLinesFile {
    param(
        [Parameter(Mandatory = $true)][string]$Sql,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $writer = New-Object IO.StreamWriter($Path, $false, $utf8NoBom)
    try {
        [void](Write-PsqlLines -Sql $Sql -Writer $writer)
    }
    finally {
        $writer.Dispose()
    }
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "未找到 docker 命令。"
    }

    $runningOutput = @(& docker inspect -f "{{.State.Running}}" $Container 2>$null)
    $inspectExitCode = $LASTEXITCODE
    $running = if ($runningOutput.Count -gt 0) { ([string]$runningOutput[0]).Trim() } else { "" }
    if ($inspectExitCode -ne 0 -or $running -ne "true") {
        throw "容器不存在或未运行：$Container"
    }

    Write-Host "正在验证旧数据库是否满足 V2 结构化导入要求..."

    $eventsWithoutEntities = Invoke-PsqlInt64 -Sql @"
SELECT count(*)
FROM events e
JOIN documents d ON d.id = e.document_id
WHERE ($documentPredicate)
  AND ($eventPredicate)
  AND NOT EXISTS (
      SELECT 1 FROM event_entities ee WHERE ee.event_id = e.id
  );
"@

    $chunksWithoutEvents = Invoke-PsqlInt64 -Sql @"
SELECT count(*)
FROM source_chunks c
JOIN documents d ON d.id = c.document_id
WHERE ($documentPredicate)
  AND NOT EXISTS (
      SELECT 1
      FROM events e
      WHERE e.chunk_id = c.id
        AND ($eventPredicate)
  );
"@

    $documentsWithoutChunks = Invoke-PsqlInt64 -Sql @"
SELECT count(*)
FROM documents d
WHERE ($documentPredicate)
  AND NOT EXISTS (
      SELECT 1
      FROM source_chunks c
      WHERE c.document_id = d.id
        AND EXISTS (
            SELECT 1
            FROM events e
            WHERE e.chunk_id = c.id
              AND ($eventPredicate)
        )
  )
  AND NOT EXISTS (
      SELECT 1
      FROM events e
      WHERE e.document_id = d.id
        AND (e.chunk_id IS NULL OR NOT EXISTS (SELECT 1 FROM source_chunks c2 WHERE c2.id = e.chunk_id))
        AND ($eventPredicate)
  );
"@

    if ($eventsWithoutEntities -gt 0) {
        throw "有 $eventsWithoutEntities 个事件没有实体，V2 会拒绝导入。请先修复旧数据。"
    }
    if ($chunksWithoutEvents -gt 0) {
        throw "有 $chunksWithoutEvents 个切片没有可导入事件。若它们只关联了已删除事件，请使用 -IncludeDeletedEvents 重试。"
    }
    if ($documentsWithoutChunks -gt 0) {
        throw "有 $documentsWithoutChunks 个文档没有可导入切片或事件。"
    }

    $projectJson = Invoke-PsqlText -Sql @"
SELECT jsonb_build_object(
    'id', s.id::text,
    'name', s.name,
    'description', COALESCE(s.description, ''),
    'metadata', COALESCE(s.metadata, '{}'::jsonb) || jsonb_build_object(
        'legacyDatabase', current_database(),
        'legacySourceId', s.id::text,
        'legacyVectorsOmitted', true,
        'migrationNote', '由 export-legacy-for-v2.ps1 从 PostgreSQL V1 导出；向量需由 V2 重建'
    ),
    'archivedAt', s.archived_at,
    'createdAt', s.created_at,
    'updatedAt', s.updated_at
)::text
FROM sources s
ORDER BY s.created_at, s.id
LIMIT 1;
"@

    if ([string]::IsNullOrWhiteSpace($projectJson)) {
        throw "旧数据库中没有 sources 记录。"
    }

    $documentText = Invoke-PsqlText -Sql @"
SELECT jsonb_build_object(
    'id', d.id::text,
    'title', d.title,
    'chunks', (
        SELECT count(*)
        FROM source_chunks c
        WHERE c.document_id = d.id
          AND EXISTS (
              SELECT 1 FROM events e WHERE e.chunk_id = c.id AND ($eventPredicate)
          )
    ),
    'events', (
        SELECT count(*) FROM events e WHERE e.document_id = d.id AND ($eventPredicate)
    ),
    'relations', (
        SELECT count(*)
        FROM event_entities ee
        JOIN events e ON e.id = ee.event_id
        WHERE e.document_id = d.id AND ($eventPredicate)
    )
)::text
FROM documents d
WHERE ($documentPredicate)
ORDER BY d.created_at, d.id;
"@
    $documentLines = @($documentText -split "`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })

    if ($documentLines.Count -eq 0) {
        throw "没有找到可导出的文档。"
    }

    $documents = @($documentLines | ForEach-Object { $_ | ConvertFrom-Json })
    $manifest = New-Object System.Collections.Generic.List[object]
    $index = 0

    foreach ($document in $documents) {
        $index += 1
        $documentId = [string]$document.id
        if ($documentId -notmatch '^[0-9a-fA-F-]{36}$') {
            throw "文档 ID 不是合法 UUID：$documentId"
        }

        $fileName = Get-SafeFileName -Index $index -Title ([string]$document.title) -Id $documentId
        $path = Join-Path $importDirectory $fileName
        Write-Host ("[{0}/{1}] 导出 {2}（切片 {3}，事件 {4}，关系 {5}）" -f $index, $documents.Count, $document.title, $document.chunks, $document.events, $document.relations)

        $documentJson = Invoke-PsqlText -Sql @"
SELECT jsonb_build_object(
    'id', d.id::text,
    'systemDocumentId', d.id::text,
    'title', d.title,
    'sourceName', COALESCE(NULLIF(d.source_name, ''), s.name, ''),
    'sourceType', COALESCE(d.source_type, ''),
    'sourceDate', COALESCE(d.source_date, ''),
    'sourceAuthor', COALESCE(d.source_author, ''),
    'sourceUrl', COALESCE(d.source_url, ''),
    'status', d.status,
    'parseStatus', d.parse_status,
    'error', d.error,
    'metadata', COALESCE(d.metadata, '{}'::jsonb),
    'archivedAt', d.archived_at,
    'createdAt', d.created_at,
    'updatedAt', d.updated_at
)::text
FROM documents d
JOIN sources s ON s.id = d.source_id
WHERE d.id = '$documentId'::uuid;
"@

        if (-not $documentJson.StartsWith("{") -or -not $documentJson.EndsWith("}")) {
            throw "无法生成文档 JSON：$documentId"
        }

        $chunkSql = @"
SELECT jsonb_build_object(
    'id', c.id::text,
    'systemChunkId', c.id::text,
    'heading', COALESCE(c.heading, ''),
    'content', c.content,
    'rawContent', COALESCE(c.raw_content, c.content),
    'rank', c.rank,
    'sourceType', c.source_type,
    'externalSourceId', c.external_source_id,
    'references', COALESCE(to_jsonb(c.references), '[]'::jsonb),
    'metadata', COALESCE(c.metadata, '{}'::jsonb) || jsonb_build_object(
        'createdAt', c.created_at,
        'updatedAt', c.updated_at,
        'legacyEmbeddingOmitted', true,
        'documentSection', (
            SELECT jsonb_strip_nulls(jsonb_build_object(
                'id', ds.id::text,
                'orderIndex', ds.order_index,
                'renderGroupIndex', ds.render_group_index,
                'type', ds.type,
                'heading', ds.heading,
                'content', ds.content,
                'rawContent', ds.raw_content,
                'imageUrl', ds.image_url,
                'metadata', ds.metadata,
                'tokenCount', ds.token_count,
                'createdAt', ds.created_at
            ))
            FROM document_sections ds
            WHERE ds.document_id = c.document_id
              AND ds.order_index = c.rank
            ORDER BY ds.id
            LIMIT 1
        )
    ),
    'events', COALESCE((
        SELECT jsonb_agg(
            jsonb_strip_nulls(jsonb_build_object(
                'id', e.id::text,
                'systemEventId', e.id::text,
                'documentId', e.document_id::text,
                'chunkId', c.id::text,
                'title', e.title,
                'summary', e.summary,
                'content', e.content,
                'category', COALESCE(e.category, ''),
                'keywords', COALESCE(to_jsonb(e.keywords), '[]'::jsonb),
                'rank', e.rank,
                'sourceType', e.source_type,
                'externalSourceId', e.external_source_id,
                'parentId', e.parent_id::text,
                'level', e.level,
                'priority', e.priority,
                'status', e.status,
                'startTime', e.start_time,
                'endTime', e.end_time,
                'references', COALESCE(to_jsonb(e.references), '[]'::jsonb),
                'metadata', COALESCE(e.metadata, '{}'::jsonb) || jsonb_build_object(
                    'legacyEmbeddingsOmitted', true
                ),
                'deletedAt', e.deleted_at,
                'createdAt', e.created_at,
                'updatedAt', e.updated_at,
                'entities', COALESCE((
                    SELECT jsonb_agg(
                        jsonb_strip_nulls(jsonb_build_object(
                            'eventId', e.id::text,
                            'entityId', en.id::text,
                            'systemEntityId', en.id::text,
                            'entityType', en.type,
                            'entityName', en.name,
                            'normalizedName', en.normalized_name,
                            'entityDescription', COALESCE(en.description, ''),
                            'relationDescription', COALESCE(ee.description, ''),
                            'valueType', en.value_type,
                            'valueRaw', en.value_raw,
                            'intValue', en.int_value,
                            'numericValue', en.numeric_value,
                            'datetimeValue', en.datetime_value,
                            'boolValue', en.bool_value,
                            'enumValue', en.enum_value,
                            'valueUnit', en.value_unit,
                            'valueConfidence', en.value_confidence,
                            'metadata', COALESCE(en.metadata, '{}'::jsonb) || jsonb_build_object(
                                'legacyEmbeddingOmitted', true
                            ),
                            'createdAt', en.created_at,
                            'updatedAt', en.updated_at,
                            'relationWeight', ee.weight,
                            'relationMetadata', ee.metadata,
                            'relationCreatedAt', ee.created_at,
                            'entityTypeDefinition', jsonb_strip_nulls(jsonb_build_object(
                                'id', et.id::text,
                                'scope', et.scope,
                                'type', et.type,
                                'name', et.name,
                                'description', et.description,
                                'weight', et.weight,
                                'similarityThreshold', et.similarity_threshold,
                                'valueConstraints', et.value_constraints,
                                'isDefault', et.is_default,
                                'isActive', et.is_active,
                                'createdAt', et.created_at,
                                'updatedAt', et.updated_at
                            ))
                        ))
                        ORDER BY ee.created_at, ee.id
                    )
                    FROM event_entities ee
                    JOIN entities en ON en.id = ee.entity_id
                    LEFT JOIN entity_types et ON et.id = en.entity_type_id
                    WHERE ee.event_id = e.id
                ), '[]'::jsonb)
            ))
            ORDER BY e.rank, e.id
        )
        FROM events e
        WHERE e.chunk_id = c.id
          AND ($eventPredicate)
    ), '[]'::jsonb)
)::text
FROM source_chunks c
WHERE c.document_id = '$documentId'::uuid
  AND EXISTS (
      SELECT 1
      FROM events e
      WHERE e.chunk_id = c.id
        AND ($eventPredicate)
  )
ORDER BY c.rank, c.id;
"@

        $orphanEventSql = @"
SELECT jsonb_strip_nulls(jsonb_build_object(
    'id', e.id::text,
    'systemEventId', e.id::text,
    'documentId', e.document_id::text,
    'title', e.title,
    'summary', e.summary,
    'content', e.content,
    'category', COALESCE(e.category, ''),
    'keywords', COALESCE(to_jsonb(e.keywords), '[]'::jsonb),
    'rank', e.rank,
    'sourceType', e.source_type,
    'externalSourceId', e.external_source_id,
    'parentId', e.parent_id::text,
    'level', e.level,
    'priority', e.priority,
    'status', e.status,
    'startTime', e.start_time,
    'endTime', e.end_time,
    'references', COALESCE(to_jsonb(e.references), '[]'::jsonb),
    'metadata', COALESCE(e.metadata, '{}'::jsonb) || jsonb_build_object('legacyEmbeddingsOmitted', true),
    'deletedAt', e.deleted_at,
    'createdAt', e.created_at,
    'updatedAt', e.updated_at,
    'entities', COALESCE((
        SELECT jsonb_agg(
            jsonb_strip_nulls(jsonb_build_object(
                'eventId', e.id::text,
                'entityId', en.id::text,
                'systemEntityId', en.id::text,
                'entityType', en.type,
                'entityName', en.name,
                'normalizedName', en.normalized_name,
                'entityDescription', COALESCE(en.description, ''),
                'relationDescription', COALESCE(ee.description, ''),
                'valueType', en.value_type,
                'valueRaw', en.value_raw,
                'intValue', en.int_value,
                'numericValue', en.numeric_value,
                'datetimeValue', en.datetime_value,
                'boolValue', en.bool_value,
                'enumValue', en.enum_value,
                'valueUnit', en.value_unit,
                'valueConfidence', en.value_confidence,
                'metadata', COALESCE(en.metadata, '{}'::jsonb) || jsonb_build_object('legacyEmbeddingOmitted', true),
                'createdAt', en.created_at,
                'updatedAt', en.updated_at,
                'relationWeight', ee.weight,
                'relationMetadata', ee.metadata,
                'relationCreatedAt', ee.created_at
            ))
            ORDER BY ee.created_at, ee.id
        )
        FROM event_entities ee
        JOIN entities en ON en.id = ee.entity_id
        WHERE ee.event_id = e.id
    ), '[]'::jsonb)
))::text
FROM events e
WHERE e.document_id = '$documentId'::uuid
  AND (e.chunk_id IS NULL OR NOT EXISTS (SELECT 1 FROM source_chunks c WHERE c.id = e.chunk_id))
  AND ($eventPredicate)
ORDER BY e.rank, e.id;
"@

        $writer = New-Object IO.StreamWriter($path, $false, $utf8NoBom)
        try {
            $exportedAtJson = ((Get-Date).ToUniversalTime().ToString("o") | ConvertTo-Json -Compress)
            $writer.Write('{"schemaVersion":1,"exportedAt":')
            $writer.Write($exportedAtJson)
            $writer.Write(',"idPolicy":{"databaseIds":"legacy PostgreSQL UUIDs are preserved; V2 will assign system IDs","chunkKey":"legacy chunk UUID is preserved"},"project":')
            $writer.Write($projectJson)
            $writer.Write(',"documents":[')
            $writer.Write($documentJson.Substring(0, $documentJson.Length - 1))
            $writer.Write(',"chunks":[')
            [void](Write-PsqlLines -Sql $chunkSql -Writer $writer -AsJsonArrayItems)
            $writer.Write('],"eventsWithoutChunk":[')
            [void](Write-PsqlLines -Sql $orphanEventSql -Writer $writer -AsJsonArrayItems)
            $writer.Write(']}]}')
        }
        catch {
            $writer.Dispose()
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            throw
        }
        finally {
            if ($null -ne $writer) {
                $writer.Dispose()
            }
        }

        $file = Get-Item -LiteralPath $path
        $manifest.Add([pscustomobject]@{
            File = $file.Name
            LegacyDocumentId = $documentId
            Title = [string]$document.title
            Chunks = [int64]$document.chunks
            Events = [int64]$document.events
            Relations = [int64]$document.relations
            Bytes = $file.Length
            Megabytes = [Math]::Round($file.Length / 1MB, 2)
        })
    }

    $manifestPath = Join-Path $OutputDirectory "manifest.csv"
    $manifest | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding UTF8

    if (-not $SkipAuxiliaryArchive) {
        Write-Host "正在归档 V2 结构化导入不会原生恢复的辅助数据..."

        Export-JsonLinesFile -Path (Join-Path $archiveDirectory "entity-types.jsonl") -Sql @"
SELECT jsonb_strip_nulls(jsonb_build_object(
    'id', id::text,
    'sourceId', source_id::text,
    'documentId', document_id::text,
    'scope', scope,
    'type', type,
    'name', name,
    'description', description,
    'weight', weight,
    'similarityThreshold', similarity_threshold,
    'valueConstraints', value_constraints,
    'isDefault', is_default,
    'isActive', is_active,
    'createdAt', created_at,
    'updatedAt', updated_at
))::text
FROM entity_types
ORDER BY created_at, id;
"@

        Export-JsonLinesFile -Path (Join-Path $archiveDirectory "mcp-sessions.jsonl") -Sql @"
SELECT to_jsonb(t)::text FROM (
    SELECT id, tenant_id, title, status, model, source_ids, metadata, created_at, updated_at
    FROM mcp_sessions
    ORDER BY created_at, id
) t;
"@

        Export-JsonLinesFile -Path (Join-Path $archiveDirectory "mcp-messages.jsonl") -Sql @"
SELECT to_jsonb(t)::text FROM (
    SELECT id, session_id, role, content, metadata, created_at
    FROM mcp_messages
    ORDER BY created_at, id
) t;
"@

        Export-JsonLinesFile -Path (Join-Path $archiveDirectory "mcp-tool-calls.jsonl") -Sql @"
SELECT to_jsonb(t)::text FROM (
    SELECT id, session_id, message_id, tool_name, arguments, result, status, duration_ms, error, created_at
    FROM mcp_tool_calls
    ORDER BY created_at, id
) t;
"@

        Export-JsonLinesFile -Path (Join-Path $archiveDirectory "ai-provider-settings.redacted.jsonl") -Sql @"
SELECT jsonb_build_object(
    'id', id,
    'embeddingBaseUrl', embedding_base_url,
    'embeddingModel', embedding_model,
    'embeddingDimensions', embedding_dimensions,
    'embeddingApiKeyWasPresent', embedding_api_key IS NOT NULL AND embedding_api_key <> '',
    'llmBaseUrl', llm_base_url,
    'llmModel', llm_model,
    'llmApiKeyWasPresent', llm_api_key IS NOT NULL AND llm_api_key <> '',
    'llmTimeoutMs', llm_timeout_ms,
    'llmMaxRetries', llm_max_retries,
    'metadata', metadata,
    'createdAt', created_at,
    'updatedAt', updated_at,
    'secretsRedacted', true
)::text
FROM ai_provider_settings
ORDER BY created_at, id;
"@

        Export-JsonLinesFile -Path (Join-Path $archiveDirectory "schema-migrations.jsonl") -Sql @"
SELECT to_jsonb(t)::text FROM (
    SELECT name, applied_at FROM schema_migrations ORDER BY applied_at, name
) t;
"@
    }

    $totalBytes = ($manifest | Measure-Object -Property Bytes -Sum).Sum
    $readmePath = Join-Path $OutputDirectory "README-下一步.txt"
    $readme = @"
ChemClaw / SAG V1 -> V2 导出完成

导出时间：$((Get-Date).ToString("yyyy-MM-dd HH:mm:ss"))
旧容器：$Container
旧数据库：$PgDatabase
导入文件目录：$importDirectory
文档文件数：$($manifest.Count)
导入 JSON 总大小：$([Math]::Round($totalBytes / 1GB, 3)) GB

说明：
1. import 目录中的每个 JSON 都是 V2 支持的 schemaVersion: 1 文件。
2. 旧 pgvector embedding 和 tsvector 没有写入 JSON；V2 应使用当前 Embedding 配置重建 LanceDB 向量。
3. archive-not-imported 保存实体类型高级配置、MCP 历史和脱敏后的模型配置，它们不会由“结构化导入”自动恢复。
4. ai_provider_settings 中的 API Key 已故意删除，请在 V2 设置页重新填写。
5. 默认不导入 deleted_at 不为空的事件；如确实需要，可使用 -IncludeDeletedEvents 重新导出。
6. 在确认 V2 数据和备份都正常前，不要删除 Docker 容器或卷。

建议先只导入 import 目录中的第一个 JSON 做验证，确认文档、图谱和实体关系正常后，再批量导入剩余文件。
"@
    [IO.File]::WriteAllText($readmePath, $readme, $utf8NoBom)

    Write-Host ""
    Write-Host "导出完成。" -ForegroundColor Green
    Write-Host "目录：$OutputDirectory"
    Write-Host "文档文件：$($manifest.Count)"
    Write-Host ("JSON 总大小：{0:N3} GB" -f ($totalBytes / 1GB))
    Write-Host "清单：$manifestPath"
}
finally {
    [Console]::OutputEncoding = $previousConsoleEncoding
    $OutputEncoding = $previousOutputEncoding
}
