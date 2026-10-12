param(
    [string]$ImageRoot = 'F:\HJL\平武一张图\2次无人机原始\未建模',
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'scan_result')
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ImageRoot -PathType Container)) {
    throw "影像目录不存在：$ImageRoot。请在存放影像的电脑上运行，或通过 -ImageRoot 指定路径。"
}
$root = (Get-Item -LiteralPath $ImageRoot).FullName
$extensions = @('.jpg', '.jpeg', '.tif', '.tiff', '.dng')
$folders = @(Get-ChildItem -LiteralPath $root -Directory -Recurse | Where-Object { $_.Name -like 'DJI_*' -or $_.Name -match '^\d{16}$' })
$rows = @()
$issues = @()
foreach ($folder in $folders) {
    $relative = $folder.FullName.Substring($root.Length).TrimStart('\')
    $town = ($relative -split '\\')[0]
    $photos = @(Get-ChildItem -LiteralPath $folder.FullName -File -Recurse | Where-Object { $extensions -contains $_.Extension.ToLowerInvariant() })
    if ($folder.Name -notmatch '(?:^|_)(\d{16})$') {
        $issues += [pscustomobject]@{ Reason='缺少16位地点编号'; Town=$town; Folder=$folder.FullName; PhotoCount=$photos.Count }
        continue
    }
    $id = $Matches[1]
    if ($photos.Count -eq 0) {
        $issues += [pscustomobject]@{ Reason='未找到支持的照片'; Town=$town; Folder=$folder.FullName; PhotoCount=0 }
        continue
    }
    $rows += [pscustomobject]@{ SiteId=$id; Town=$town; Folder=$folder.FullName; Photos=@($photos.FullName) }
}
$tasks = @(foreach ($group in ($rows | Group-Object SiteId)) {
    $towns = @($group.Group.Town | Sort-Object -Unique)
    if ($towns.Count -gt 1) {
        foreach ($row in $group.Group) {
            $issues += [pscustomobject]@{ Reason='同一地点编号出现在多个乡镇，需确认'; Town=$row.Town; Folder=$row.Folder; PhotoCount=$row.Photos.Count }
        }
        continue
    }
    $photos = @($group.Group.Photos | Sort-Object -Unique)
    [pscustomobject]@{ SiteId=$group.Name; Town=$towns[0]; Folders=@($group.Group.Folder); PhotoCount=$photos.Count; Photos=$photos; Status='pending' }
})
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
ConvertTo-Json -InputObject $tasks -Depth 6 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'tasks.json') -Encoding UTF8
$tasks | Select-Object SiteId,Town,PhotoCount,@{Name='FolderCount';Expression={$_.Folders.Count}} | Export-Csv -LiteralPath (Join-Path $OutputDirectory 'tasks.csv') -NoTypeInformation -Encoding UTF8
ConvertTo-Json -InputObject @($issues) -Depth 4 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'issues.json') -Encoding UTF8
Write-Host "扫描完成：$($tasks.Count) 个地点，$($issues.Count) 个待确认文件夹。结果：$OutputDirectory"
