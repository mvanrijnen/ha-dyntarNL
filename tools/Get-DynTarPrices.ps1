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
# Vergelijken in UTC: rond de klokwissel is lokale tijd niet eenduidig (dubbel uur in
# oktober), en "volgend uur" moet één echt uur later zijn.
$nowUtc  = $now.ToUniversalTime()
$nextUtc = $nowUtc.AddHours(1)

# Tijdstempel → UTC. Met offset/Z (Frank, EnergyZero) exact; zonder (eon-app, easyEnergy)
# als lokale Nederlandse tijd.
function To-Utc([string]$s) {
    $dto = [datetimeoffset]::Parse($s, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeLocal)
    return $dto.UtcDateTime
}

# Formatteer een prijs als NL-getal (komma), 5 decimalen; lege string bij $null.
$NL = [Globalization.CultureInfo]::GetCultureInfo("nl-NL")
function Fmt($v) { if ($null -eq $v) { return "" } return ([double]$v).ToString("0.#####", $NL) }

# Zoek het uur-slot dat $when (UTC) bevat.
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
                $res[$e] += New-Slot (To-Utc $t.startDateTime) (To-Utc $t.endDateTime) `
                    $g["MARKET_PRICE"] ([double]$t.totalAmount) $g["PURCHASING_FEE"]
            }
        }
    }
    return $res
}

function Get-Frank {
    # Sinds eind 2026 weigert Frank de oude marketPricesElectricity/marketPricesGas-query
    # ("Graphql validation error"); nu marketPrices per dag met een resolutie.
    $fields = "from till marketPrice marketPriceTax sourcingMarkupPrice energyTaxPrice"
    $q = "query MarketPrices(`$date: String!, `$resolution: PriceResolution!) { marketPrices(date: `$date, resolution: `$resolution) { electricityPrices { $fields } gasPrices { $fields } } }"
    $res = @{ electricity = @(); gas = @() }
    foreach ($off in -1, 0, 1) {
        $day = $now.AddDays($off).ToString("yyyy-MM-dd")
        $body = @{ operationName = "MarketPrices"; query = $q; variables = @{ date = $day; resolution = "PT60M" } } | ConvertTo-Json -Depth 4
        $resp = Invoke-RestMethod -Uri "https://graphql.frankenergie.nl" -Method Post -ContentType "application/json" -Body $body
        if ($resp.errors -and $off -eq 0) { throw "Frank: $($resp.errors[0].message)" }  # morgen 'nog niet gepubliceerd' is normaal
        $data = $resp.data.marketPrices
        if (-not $data) { continue }
        foreach ($pair in @(@("electricity", $data.electricityPrices), @("gas", $data.gasPrices))) {
            foreach ($r in $pair[1]) {
                $mex = [double]$r.marketPrice; $market = $mex + [double]$r.marketPriceTax
                # sourcingMarkupPrice en energyTaxPrice zijn AL incl. btw.
                $fee = [double]$r.sourcingMarkupPrice; $tax = [double]$r.energyTaxPrice
                $res[$pair[0]] += New-Slot (To-Utc $r.from) (To-Utc $r.till) `
                    ([math]::Round($market,5)) ([math]::Round($market + $fee + $tax,5)) ([math]::Round($fee,5))
            }
        }
    }
    return $res
}

function Get-EnergyZero {
    # Lokale middernacht (gisteren) t/m einde van morgen, omgezet naar UTC.
    $from = $now.Date.AddDays(-1).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.000Z")
    $till = $now.Date.AddDays(2).ToUniversalTime().AddMilliseconds(-1).ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    $res = @{ electricity = @(); gas = @() }
    foreach ($pair in @(@("electricity", 1, $TAX_ELEC_EX), @("gas", 2, $TAX_GAS_EX))) {
        $url = "https://api.energyzero.nl/v1/energyprices?fromDate=$from&tillDate=$till&interval=4&usageType=$($pair[1])&inclBtw=false"
        $data = Invoke-RestMethod -Uri $url
        $tax = [math]::Round($pair[2] * $VAT, 5)
        foreach ($r in $data.Prices) {
            $start = To-Utc $r.readingDate
            $market = [math]::Round([double]$r.price * $VAT, 5)
            $res[$pair[0]] += New-Slot $start $start.AddHours(1) $market ([math]::Round($market + $tax,5)) 0.0
        }
    }
    return $res
}

function Get-EasyEnergy {
    $s = $now.AddDays(-1).ToString("yyyy-MM-dd"); $e = $now.AddDays(2).ToString("yyyy-MM-dd")
    $res = @{ electricity = @(); gas = @() }
    # Gas per uur: 'day' geeft kalenderdaggemiddelden (00–00) i.p.v. de gasdag (06–06).
    foreach ($pair in @(@("electricity", "hour"), @("gas", "hour"))) {
        $url = "https://price-graph.mijn.easyenergy.com/api/prices?start=$s&end=$e&type=$($pair[0])&granularity=$($pair[1])"
        $data = Invoke-RestMethod -Uri $url
        foreach ($r in $data.prices) {
            # easyEnergy geeft opslag (purchasePrice) en all-in (invoicePrice) mee.
            $market = [double]$r.priceIncVat; $tax = [double]$r.energyTax; $fee = [double]$r.purchasePrice
            $allin = if ($r.invoicePrice) { [double]$r.invoicePrice } else { $market + $fee + $tax }
            $res[$pair[0]] += New-Slot (To-Utc $r.from) (To-Utc $r.until) `
                ([math]::Round($market,5)) ([math]::Round($allin,5)) ([math]::Round($fee,5))
        }
    }
    return $res
}

# ---------------- Verzamelen ----------------

$suppliers = [ordered]@{
    "Essent / Energiedirect (eon-app)"        = { Get-EonApp "essent.nl" }
    "Frank Energie (frank)"                   = { Get-Frank }
    "EnergyZero (marktprijs, ref)"            = { Get-EnergyZero }
    "EasyEnergie (easyenergy)"                = { Get-EasyEnergy }
}

$rows = foreach ($name in $suppliers.Keys) {
    Write-Host "Ophalen: $name ..." -ForegroundColor Cyan
    try { $data = & $suppliers[$name] } catch { Write-Warning "$name overgeslagen: $($_.Exception.Message)"; continue }
    foreach ($pair in @(@("electricity", "Stroom"), @("gas", "Gas"))) {
        $cur = Get-Slot $data[$pair[0]] $nowUtc
        $nxt = Get-Slot $data[$pair[0]] $nextUtc
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
