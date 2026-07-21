[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceId,

    [Parameter(Mandatory = $true)]
    [string]$Token,

    [Parameter(Mandatory = $true)]
    [string]$InputDirectory,

    [string]$ApiBase = "http://localhost:8100",
    [int]$PollSeconds = 3,
    [switch]$WaitForVectorJobs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ApiBase = $ApiBase.TrimEnd("/")
$InputDirectory = [IO.Path]::GetFullPath($InputDirectory)

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
    throw "未找到 curl.exe。Windows 10/11 通常已内置。"
}
if (-not (Test-Path -LiteralPath $InputDirectory -PathType Container)) {
    throw "导入目录不存在：$InputDirectory"
}

$files = @(Get-ChildItem -LiteralPath $InputDirectory -Filter "*.json" -File | Sort-Object Name)
if ($files.Count -eq 0) {
    throw "目录中没有 JSON 文件：$InputDirectory"
}

function Invoke-CurlJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    # Windows PowerShell 5.1 may decode curl UTF-8 stdout with the local
    # ANSI/OEM code page. Chinese bytes can then consume JSON quote bytes and
    # make otherwise valid JSON impossible to parse. Save the raw response to
    # a file and decode it explicitly as UTF-8 instead.
    $responseFile = [IO.Path]::GetTempFileName()
    try {
        $curlArguments = @("--output", $responseFile) + @($Arguments)
        & curl.exe @curlArguments
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "curl.exe 执行失败，退出码：$exitCode"
        }

        $joined = [IO.File]::ReadAllText($responseFile, [Text.Encoding]::UTF8).Trim()
        if ([string]::IsNullOrWhiteSpace($joined)) {
            throw "API 返回空响应。"
        }
        try {
            return ($joined | ConvertFrom-Json)
        }
        catch {
            throw "API 返回的不是有效 JSON：$joined"
        }
    }
    finally {
        Remove-Item -LiteralPath $responseFile -Force -ErrorAction SilentlyContinue
    }
}

function Wait-Job {
    param(
        [Parameter(Mandatory = $true)][string]$JobId,
        [Parameter(Mandatory = $true)][string]$Label
    )

    while ($true) {
        Start-Sleep -Seconds $PollSeconds
        $job = Invoke-CurlJson -Arguments @(
            "--silent", "--show-error", "--fail",
            "-H", "Authorization: Bearer $Token",
            "$ApiBase/api/v1/jobs/$JobId"
        )
        $percent = [Math]::Min(100, [Math]::Max(0, [int]([double]$job.progress * 100)))
        Write-Progress -Activity $Label -Status ([string]$job.status) -PercentComplete $percent

        if ($job.status -eq "succeeded") {
            Write-Progress -Activity $Label -Completed
            return $job
        }
        if ($job.status -eq "failed") {
            Write-Progress -Activity $Label -Completed
            $message = if ($job.error) { [string]$job.error } else { "未知错误" }
            throw "$Label 失败：$message"
        }
    }
}

[void](Invoke-CurlJson -Arguments @(
    "--silent", "--show-error", "--fail",
    "-H", "Authorization: Bearer $Token",
    "$ApiBase/api/v1/auth/me"
))

$source = Invoke-CurlJson -Arguments @(
    "--silent", "--show-error", "--fail",
    "-H", "Authorization: Bearer $Token",
    "$ApiBase/api/v1/sources/$SourceId"
)
Write-Host "目标项目：$($source.name) ($SourceId)"
Write-Host "待导入文件：$($files.Count)"

$summary = New-Object System.Collections.Generic.List[object]
$index = 0
foreach ($file in $files) {
    $index += 1
    Write-Host ("[{0}/{1}] 上传 {2} ({3:N2} MB)" -f $index, $files.Count, $file.Name, ($file.Length / 1MB))

    $job = Invoke-CurlJson -Arguments @(
        "--silent", "--show-error", "--fail",
        "-X", "POST",
        "-H", "Authorization: Bearer $Token",
        "-F", "file=@$($file.FullName);type=application/json",
        "$ApiBase/api/v1/sources/$SourceId/structured-import?format=json"
    )

    $completed = Wait-Job -JobId ([string]$job.id) -Label ("导入 " + $file.Name)
    $counts = $completed.payload.result.counts
    $vectorJobId = [string]$completed.payload.vector_job_id

    if ($WaitForVectorJobs -and -not [string]::IsNullOrWhiteSpace($vectorJobId)) {
        [void](Wait-Job -JobId $vectorJobId -Label ("向量索引 " + $file.Name))
    }

    $summary.Add([pscustomobject]@{
        File = $file.Name
        Documents = [int64]$counts.documents
        Chunks = [int64]$counts.chunks
        Events = [int64]$counts.events
        EntitiesCreated = [int64]$counts.entities
        Relations = [int64]$counts.relations
        VectorJobId = $vectorJobId
    })

    Write-Host ("完成：文档 {0}，切片 {1}，事件 {2}，新增实体 {3}，关系 {4}" -f $counts.documents, $counts.chunks, $counts.events, $counts.entities, $counts.relations) -ForegroundColor Green
}

$summaryPath = Join-Path (Split-Path -Parent $InputDirectory) "v2-import-result.csv"
$summary | Export-Csv -LiteralPath $summaryPath -NoTypeInformation -Encoding UTF8
Write-Host ""
Write-Host "全部导入任务完成。结果：$summaryPath" -ForegroundColor Green
