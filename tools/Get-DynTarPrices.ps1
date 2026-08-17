<#
.SYNOPSIS
    Haalt de dynamische tarieven van meerdere NL-leveranciers op en schrijft ze naar CSV.

.DESCRIPTION
    Standalone hulpscript (los van de Home Assistant integratie). Per platform worden
    de publieke prijs-API's bevraagd en de huidige + komende uurprijs berekend voor
    stroom en gas: kale beursprijs, all-in prijs en opslag (alle incl. btw).

.EXAMPLE
    .\Get-DynTarPrices.ps1
    .\Get-DynTarPrices.ps1 -Path prijzen.csv
#>
[CmdletBinding()]
param(
    [string]$Path = "dyntar_prijzen.csv"
)

$ErrorActionPreference = "Stop"
$VAT = 1.21
# Indicatieve NL energiebelasting 2026 (excl. btw), voor bronnen zonder eigen belasting.
$TAX_ELEC_EX = 0.09161
$TAX_GAS_EX  = 0.60066

$now  = Get-Date
$next = $now.AddHours(1)

function To-Local([string]$s) { return [datetimeoffset]::Parse($s).LocalDateTime }

# Formatteer een prijs als NL-getal (komma), 5 decimalen; lege string bij $null.
$NL = [Globalization.CultureInfo]::GetCultureInfo("nl-NL")
function Fmt($v) { if ($null -eq $v) { return "" } return ([double]$v).ToString("0.#####", $NL) }

# Zoek het uur-slot dat $when bevat.
function Get-Slot($slots, [datetime]$when) {
    foreach ($s in $slots) { if ($when -ge $s.Start -and $when -lt $s.End) { return $s } }
    return $null
}

function New-Slot($start, $end, $beurs, $allin, $opslag) {
    [pscustomobject]@{ Start = $start; End = $end; Beurs = $beurs; AllIn = $allin; Opslag = $opslag }
}

# ---------------- Platform-parsers ----------------

function Get-EonApp([string]$Domain) {
    $url = "https://www.$Domain/api/public/dynamicpricing/dynamic-prices/v1"
    $data = Invoke-RestMethod -Uri $url -Headers @{ "x-request-origin" = "client" }
    $res = @{ electricity = @(); gas = @() }
    foreach ($day in $data.prices) {
        foreach ($e in "electricity", "gas") {
            $block = $day.$e
            if (-not $block -or -not $block.tariffs) { continue }
            foreach ($t in $block.tariffs) {
                $g = @{}; foreach ($grp in $t.groups) { $g[$grp.type] = [double]$grp.amount }
                $res[$e] += New-Slot (To-Local $t.startDateTime) (To-Local $t.endDateTime) `
                    $g["MARKET_PRICE"] ([double]$t.totalAmount) $g["PURCHASING_FEE"]
            }
        }
    }
    return $res
}

function Get-Frank {
    $fields = "from till marketPrice marketPriceTax sourcingMarkupPrice energyTaxPrice"
    $res = @{ electricity = @(); gas = @() }
    foreach ($off in -1, 0, 1) {
        $s = $now.AddDays($off).ToString("yyyy-MM-dd")
        $en = $now.AddDays($off + 1).ToString("yyyy-MM-dd")
        $q = "query{marketPricesElectricity(startDate:`"$s`",endDate:`"$en`"){$fields} marketPricesGas(startDate:`"$s`",endDate:`"$en`"){$fields}}"
        $body = @{ query = $q } | ConvertTo-Json
        $data = (Invoke-RestMethod -Uri "https://graphql.frankenergie.nl" -Method Post -ContentType "application/json" -Body $body).data
        foreach ($pair in @(@("electricity", $data.marketPricesElectricity), @("gas", $data.marketPricesGas))) {
            foreach ($r in $pair[1]) {
                $mex = [double]$r.marketPrice; $market = $mex + [double]$r.marketPriceTax
                # sourcingMarkupPrice en energyTaxPrice zijn AL incl. btw.
                $fee = [double]$r.sourcingMarkupPrice; $tax = [double]$r.energyTaxPrice
                $res[$pair[0]] += New-Slot (To-Local $r.from) (To-Local $r.till) `
                    ([math]::Round($market,5)) ([math]::Round($market + $fee + $tax,5)) ([math]::Round($fee,5))
            }
        }
    }
    return $res
}

function Get-EnergyZero {
    $from = $now.AddDays(-1).ToString("yyyy-MM-ddT00:00:00.000Z")
    $till = $now.AddDays(1).ToString("yyyy-MM-ddT23:59:59.999Z")
    $res = @{ electricity = @(); gas = @() }
    foreach ($pair in @(@("electricity", 1, $TAX_ELEC_EX), @("gas", 2, $TAX_GAS_EX))) {
        $url = "https://api.energyzero.nl/v1/energyprices?fromDate=$from&tillDate=$till&interval=4&usageType=$($pair[1])&inclBtw=false"
        $data = Invoke-RestMethod -Uri $url
        $tax = [math]::Round($pair[2] * $VAT, 5)
        foreach ($r in $data.Prices) {
            $start = To-Local $r.readingDate
            $market = [math]::Round([double]$r.price * $VAT, 5)
            $res[$pair[0]] += New-Slot $start $start.AddHours(1) $market ([math]::Round($market + $tax,5)) 0.0
        }
    }
    return $res
}

function Get-EasyEnergy {
    $s = $now.AddDays(-1).ToString("yyyy-MM-dd"); $e = $now.AddDays(2).ToString("yyyy-MM-dd")
    $res = @{ electricity = @(); gas = @() }
    foreach ($pair in @(@("electricity", "hour"), @("gas", "day"))) {
        $url = "https://price-graph.mijn.easyenergy.com/api/prices?start=$s&end=$e&type=$($pair[0])&granularity=$($pair[1])"
        $data = Invoke-RestMethod -Uri $url
        foreach ($r in $data.prices) {
            $market = [double]$r.priceIncVat; $tax = [double]$r.energyTax
            $res[$pair[0]] += New-Slot (To-Local $r.from) (To-Local $r.until) `
                ([math]::Round($market,5)) ([math]::Round($market + $tax,5)) 0.0
        }
    }
    return $res
}

# ---------------- Verzamelen ----------------

$suppliers = [ordered]@{
    "Essent / Energiedirect (eon-app)"        = { Get-EonApp "essent.nl" }
    "Frank Energie (frank)"                   = { Get-Frank }
    "ANWB e.a. (energyzero)"                  = { Get-EnergyZero }
    "Nieuwestroom / EasyEnergie (easyenergy)" = { Get-EasyEnergy }
}

$rows = foreach ($name in $suppliers.Keys) {
    Write-Host "Ophalen: $name ..." -ForegroundColor Cyan
    try { $data = & $suppliers[$name] } catch { Write-Warning "$name overgeslagen: $($_.Exception.Message)"; continue }
    foreach ($pair in @(@("electricity", "Stroom"), @("gas", "Gas"))) {
        $cur = Get-Slot $data[$pair[0]] $now
        $nxt = Get-Slot $data[$pair[0]] $next
        [pscustomobject]@{
            Tijdstip    = $now.ToString("yyyy-MM-dd HH:mm")
            Leverancier = $name
            Energie     = $pair[1]
            BeursNu     = if ($cur) { Fmt $cur.Beurs }  else { "" }
            AllInNu     = if ($cur) { Fmt $cur.AllIn }  else { "" }
            OpslagNu    = if ($cur) { Fmt $cur.Opslag } else { "" }
            BeursVolgend  = if ($nxt) { Fmt $nxt.Beurs }  else { "" }
            AllInVolgend  = if ($nxt) { Fmt $nxt.AllIn }  else { "" }
            OpslagVolgend = if ($nxt) { Fmt $nxt.Opslag } else { "" }
        }
    }
}

$rows | Format-Table -AutoSize
$rows | Export-Csv -Path $Path -NoTypeInformation -Encoding UTF8 -Delimiter ";"
Write-Host "`nCSV opgeslagen: $Path" -ForegroundColor Green
