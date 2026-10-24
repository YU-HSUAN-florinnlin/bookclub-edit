# 讀書會剪輯：縮小 Ubuntu 的虛擬硬碟（ext4.vhdx），把 Ubuntu 裡清出來的空間還給 C 槽。
#
# 用法：在「縮小虛擬硬碟.cmd」上按右鍵，選「以系統管理員身分執行」（.cmd 只負責叫這支）。
# 流程（10-25 舊 Windows 實測有效：15.02 GB → 13.06 GB）：
#   Ubuntu 裡 fstrim → wsl --shutdown → diskpart：select vdisk / attach vdisk readonly / compact vdisk / detach vdisk
# 不用 sparse（WSL 的「自動還空間」設定）：實測不會還空間、官方也標成有資料損毀風險；sparse 開著時 compact 會失敗，
# 所以遇到 sparse 會先關掉（wsl --manage <名稱> --set-sparse false）。
#
# 這個檔要存成 UTF-8（有 BOM），Windows PowerShell 5.1 才讀得懂中文。

# 不用 'Stop'：Windows PowerShell 5.1 會把 wsl.exe／diskpart 寫到錯誤輸出的字當成例外丟出來
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:WSL_UTF8 = '1'          # 讓 wsl.exe 的輸出用 UTF-8（舊版 wsl 不認得也沒關係，下面會去掉 \0）

$Tail = '請將錯誤訊息通知開發者。'

function Say([string]$text, [string]$color = '') {
    if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text }
}

function Step([string]$text) { Write-Host ''; Write-Host "▶ $text" -ForegroundColor Cyan }

function Wait-Key {
    Write-Host ''
    Write-Host '按任意鍵關閉這個視窗…'
    try { [void][Console]::ReadKey($true) } catch { Read-Host | Out-Null }
}

function Stop-Here([string]$text) {
    Write-Host ''
    Write-Host $text -ForegroundColor Yellow
    Wait-Key
    exit 0
}

function Human([double]$bytes) {
    if ($bytes -ge 1GB) { return ('{0:N2} GB' -f ($bytes / 1GB)) }
    if ($bytes -ge 1MB) { return ('{0:N0} MB' -f ($bytes / 1MB)) }
    return ('{0:N0} KB' -f ($bytes / 1KB))
}

# wsl.exe 的輸出：去掉 UTF-16 留下的 \0、BOM、空行
function Wsl-Lines([object[]]$raw) {
    $out = @()
    foreach ($l in $raw) {
        $t = ([string]$l) -replace "`0", '' -replace [char]0xFEFF, ''
        $t = $t.Trim()
        if ($t) { $out += $t }
    }
    return $out
}

# 檔案在磁碟上實際佔多少（sparse 檔也對；只看 Length 會把沒寫的部分也算進去）
Add-Type -Namespace Bookclub -Name Disk -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
public static extern uint GetCompressedFileSizeW(string lpFileName, out uint lpFileSizeHigh);
'@

function OnDisk([string]$path) {
    $high = [uint32]0
    $low = [Bookclub.Disk]::GetCompressedFileSizeW($path, [ref]$high)
    if ($low -eq [uint32]::MaxValue -and [Runtime.InteropServices.Marshal]::GetLastWin32Error() -ne 0) {
        return (Get-Item -LiteralPath $path).Length
    }
    return ([double]$high * 4294967296.0) + [double]$low
}

function FreeSpace([string]$path) {
    $root = [IO.Path]::GetPathRoot($path)
    return (New-Object IO.DriveInfo($root)).AvailableFreeSpace
}

function DriveName([string]$path) {
    $root = [IO.Path]::GetPathRoot($path)
    if ($root -match '^([A-Za-z]):') { return "$($Matches[1].ToUpper()) 槽" }
    return '磁碟'
}

# 找 WSL 的發行版：先讀登錄（新舊版 WSL 都會寫），找不到再找常見位置的 ext4.vhdx
function Find-Distros {
    $list = @()
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (Test-Path $key) {
        foreach ($k in Get-ChildItem $key -ErrorAction SilentlyContinue) {
            $p = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
            if (-not $p -or -not $p.DistributionName -or -not $p.BasePath) { continue }
            $base = [string]$p.BasePath
            if ($base.StartsWith('\\?\')) { $base = $base.Substring(4) }
            # 新版 WSL 有 VhdFileName（用 --import --vhd 匯入的檔名可能不是 ext4.vhdx）
            $file = 'ext4.vhdx'
            if ($p.PSObject.Properties['VhdFileName'] -and $p.VhdFileName) { $file = [string]$p.VhdFileName }
            $vhdx = Join-Path $base $file
            if (Test-Path -LiteralPath $vhdx) {
                $list += [pscustomobject]@{ Name = [string]$p.DistributionName; Label = [string]$p.DistributionName; Vhdx = $vhdx }
            }
        }
    }
    if ($list.Count -eq 0) {
        $cands = @()
        $cands += Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'wsl') -Filter 'ext4.vhdx' -Recurse -Depth 1 -ErrorAction SilentlyContinue
        $cands += Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Packages\*\LocalState\ext4.vhdx') -ErrorAction SilentlyContinue
        # 登錄讀不到時沒有名稱：用資料夾名稱讓人分得出是哪一個
        foreach ($c in $cands) {
            $folder = Split-Path -Leaf (Split-Path -Parent $c.FullName)
            if ($folder -eq 'LocalState') { $folder = Split-Path -Leaf (Split-Path -Parent (Split-Path -Parent $c.FullName)) }
            $list += [pscustomobject]@{ Name = ''; Label = "資料夾 $folder"; Vhdx = $c.FullName }
        }
    }
    return $list
}

# 讀書會剪輯裝過的那個 Ubuntu 會有 ~/.cache/bookclub（AI 聲音模型）。任何一個帳號有就算（審查 #2：以前用 ls -d 兩個路徑，
# 少一個就回失敗，永遠判斷成沒有）。用 -e 直接執行 sh、不經過預設的 shell，字串裡不放雙引號（PowerShell 5.1 傳給外部程式會弄丟）
$HasToolSh = 'for d in /home/*/.cache/bookclub /root/.cache/bookclub; do [ -d $d ] && exit 0; done; exit 1'
function Has-Tool([string]$name) {
    $null = & wsl.exe -d $name -u root -e sh -c $HasToolSh
    return ($LASTEXITCODE -eq 0)
}

# 還在跑的發行版（只算登錄裡認得的名字；「沒有正在執行的發行版」這類說明文字不算）
function Running-Distros([string[]]$names) {
    $raw = & wsl.exe --list --running --quiet 2>&1
    $lines = Wsl-Lines $raw
    if (-not $names -or $names.Count -eq 0) { return ,@($lines | Where-Object { $_ -match 'ubuntu' }) }
    return ,@($lines | Where-Object { $names -contains $_ })
}

function Run-Diskpart([string[]]$lines) {
    $tmp = Join-Path $env:TEMP ("bookclub-diskpart-{0}.txt" -f ([guid]::NewGuid().ToString('N')))
    # diskpart 讀腳本用系統的舊編碼（中文 Windows 是 Big5），所以用 Default 編碼寫
    [IO.File]::WriteAllLines($tmp, $lines, [Text.Encoding]::Default)
    try {
        # Start-Process -NoNewWindow：diskpart 的字直接印在這個視窗（中文不會被轉壞，失敗時可以截圖），結束碼另外拿
        $p = Start-Process -FilePath 'diskpart.exe' -ArgumentList "/s `"$tmp`"" -NoNewWindow -Wait -PassThru
        return $p.ExitCode
    } finally {
        Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
    }
}

try {
    Say '讀書會剪輯：縮小虛擬硬碟' 'Green'
    Say '把 Ubuntu 裡清出來的空間還給 Windows（通常是 C 槽）。大約要幾分鐘，跑的時候不要關掉這個視窗。'

    # a. 要系統管理員身分（diskpart 需要）
    $me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Stop-Here '請關掉這個視窗，在檔案上按右鍵選「以系統管理員身分執行」。'
    }

    # b. 第 4 步正在跑就不要關 Ubuntu
    Step '確認讀書會剪輯工具沒有在跑第 4 步'
    # 只有「連線被拒」才算工具沒開；逾時（第 4 步跑起來電腦很忙時會慢）或其他錯誤一律當成不確定，問人（審查 #1）
    # 用 127.0.0.1 不用 localhost：localhost 會先試 IPv6，多等一段（審查 #4）
    $toolState = 'unknown'
    try {
        $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:8766/api/run/status' -UseBasicParsing -TimeoutSec 10
        $txt = [Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
        if ($txt -match '"第4步執行中"\s*:\s*true' -or $txt -match '"\\u7b2c4\\u6b65\\u57f7\\u884c\\u4e2d"\s*:\s*true') { $toolState = 'busy' }
        elseif ($txt -match '"第4步執行中"\s*:\s*false' -or $txt -match '"\\u7b2c4\\u6b65\\u57f7\\u884c\\u4e2d"\s*:\s*false') { $toolState = 'idle' }
    } catch {
        $ex = $_.Exception
        while ($ex -and -not ($ex -is [Net.WebException]) -and $ex.InnerException) { $ex = $ex.InnerException }
        if ($ex -is [Net.WebException] -and $ex.Status -eq [Net.WebExceptionStatus]::ConnectFailure) { $toolState = 'off' }
    }
    if ($toolState -eq 'busy') { Stop-Here '第 4 步正在跑，等它跑完再縮小。' }
    elseif ($toolState -eq 'idle') { Say '  工具開著，沒有在跑第 4 步（等一下會被關掉）。' }
    elseif ($toolState -eq 'off') { Say '  工具沒有開著。' }
    else {
        Say '  看不出工具有沒有在跑第 4 步（工具沒有回應，可能正在忙）。' 'Yellow'
        Say '  第 4 步現在有沒有在跑？有在跑請直接關掉這個視窗。' 'Yellow'
        Read-Host '  確定沒有在跑，按 Enter 繼續' | Out-Null
    }

    # c. 找 Ubuntu 和它的虛擬硬碟
    Step '找 Ubuntu 的虛擬硬碟'
    $all = @(Find-Distros | Where-Object { $_.Name -notlike 'docker-desktop*' })
    if ($all.Count -eq 0) { Stop-Here "找不到 Ubuntu 的虛擬硬碟（ext4.vhdx）。$Tail" }
    $pick = @($all | Where-Object { $_.Name -match 'ubuntu' })
    $mustAsk = $false
    if ($pick.Count -eq 0) {
        # 名稱都沒有 Ubuntu（例如登錄讀不到、或是別的 Linux）：不自己猜，一律請人選（審查 #6）
        $pick = $all
        $mustAsk = $true
    }
    if ($pick.Count -gt 1) {
        $withTool = @($pick | Where-Object { $_.Name -and (Has-Tool $_.Name) })
        if ($withTool.Count -ge 1) {
            Say ("  有裝讀書會剪輯工具的：{0}" -f (($withTool | ForEach-Object { $_.Label }) -join '、'))
            $pick = $withTool
        }
    }
    if ($pick.Count -gt 1 -or $mustAsk) {
        Say '  請選要縮小哪一個：'
        for ($i = 0; $i -lt $pick.Count; $i++) {
            Say ("    {0}. {1}（{2}）" -f ($i + 1), $pick[$i].Label, (Human (OnDisk $pick[$i].Vhdx)))
        }
        $n = 0
        while ($n -lt 1 -or $n -gt $pick.Count) {
            $ans = Read-Host '  打數字後按 Enter'
            [void][int]::TryParse($ans, [ref]$n)
        }
        $pick = @($pick[$n - 1])
    }
    $distro = $pick[0].Name
    $vhdx = $pick[0].Vhdx
    $drive = DriveName $vhdx
    Say ("  {0}：{1}" -f $pick[0].Label, $vhdx)
    if (-not $distro) { Say '  （讀不到這個 Ubuntu 的名稱，整理空間那一步會用預設的 Ubuntu）' }

    # d. 縮小前
    $before = OnDisk $vhdx
    $freeBefore = FreeSpace $vhdx
    Say ("  虛擬硬碟現在佔 {0}，{1}還剩 {2}" -f (Human $before), $drive, (Human $freeBefore))

    Write-Host ''
    Say '接下來會關掉 Ubuntu 和其他在 WSL 裡跑的程式（讀書會剪輯工具也會一起關掉）。' 'Yellow'
    Say '第 4 步正在跑的話，請直接關掉這個視窗，等它跑完再來。' 'Yellow'
    Read-Host '按 Enter 開始；不想縮小就直接關掉這個視窗' | Out-Null

    # e. 先在 Ubuntu 裡把刪掉的空間標出來（root 執行，不用打密碼）
    Step '整理 Ubuntu 裡的空間（fstrim）'
    if ($distro) { & wsl.exe -d $distro -u root -- fstrim -av } else { & wsl.exe -u root -- fstrim -av }
    if ($LASTEXITCODE -ne 0) { Say '  這一步沒有成功，繼續縮小（可能縮得比較少）。' 'Yellow' }

    # f. sparse 開著時 compact 會失敗 → 先關掉
    $sparse = ((Get-Item -LiteralPath $vhdx).Attributes -band [IO.FileAttributes]::SparseFile) -ne 0
    if ($sparse) {
        Step '關掉「自動還空間」（sparse）設定'
        Say '  這個虛擬硬碟開著 sparse，開著的時候沒辦法縮小，而且它實際上不會還空間，所以把它關掉。'
        & wsl.exe --shutdown
        if ($distro) {
            & wsl.exe --manage $distro --set-sparse false
            if ($LASTEXITCODE -ne 0) { Stop-Here "sparse 設定關不掉，沒有縮小。$Tail" }
        } else {
            Stop-Here "這個虛擬硬碟開著 sparse，但找不到 Ubuntu 的名稱，沒辦法關掉，沒有縮小。$Tail"
        }
    }

    # g. 關掉 Ubuntu，確認都停了
    Step '關掉 Ubuntu'
    & wsl.exe --shutdown
    $waited = 0
    $names = @($all | ForEach-Object { $_.Name } | Where-Object { $_ })
    while ((Running-Distros $names).Count -gt 0 -and $waited -lt 30) { Start-Sleep -Seconds 2; $waited += 2 }
    if ((Running-Distros $names).Count -gt 0) {
        Stop-Here "Ubuntu 關不掉（等了 30 秒還在跑），沒有縮小。請重新開機後再試一次；還是不行的話，$Tail"
    }
    Say '  Ubuntu 已經關掉了。'

    # h. diskpart 縮小；不論成功失敗最後都要卸下虛擬硬碟
    Step '縮小虛擬硬碟（diskpart，要一兩分鐘）'
    $target = $vhdx
    try {
        $short = (New-Object -ComObject Scripting.FileSystemObject).GetFile($vhdx).ShortPath
        if ($short -and $short -match '^[\x20-\x7E]+$') { $target = $short }   # 路徑有中文時改用短檔名，比較穩
    } catch { }
    $code = Run-Diskpart @("select vdisk file=`"$target`"", 'attach vdisk readonly', 'compact vdisk', 'detach vdisk')
    if ($code -ne 0) {
        Say '正在卸下虛擬硬碟…' 'Yellow'
        $null = Run-Diskpart @("select vdisk file=`"$target`"", 'detach vdisk noerr')
        Write-Host ''
        Say "縮小沒有成功（diskpart 結束碼 $code）。上面是 diskpart 的原文，請截圖這個視窗。$Tail" 'Red'
        Say '虛擬硬碟已經卸下，Ubuntu 可以照常打開。' 'Yellow'
        Wait-Key
        exit 0
    }

    # i. 縮小後
    $after = OnDisk $vhdx
    $freeAfter = FreeSpace $vhdx
    Write-Host ''
    Say '縮小完成。' 'Green'
    Say ("  虛擬硬碟：{0} → {1}（空出 {2}）" -f (Human $before), (Human $after), (Human ([Math]::Max(0, $before - $after))))
    Say ("  {0}還剩 {1}" -f $drive, (Human $freeAfter))
    Write-Host ''
    Say '工具已經關掉了，要用的話請重新開（照平常的方式）。'
    Wait-Key
    exit 0
} catch {
    Write-Host ''
    Write-Host "出錯了：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host $Tail -ForegroundColor Red
    Wait-Key
    exit 0
}
