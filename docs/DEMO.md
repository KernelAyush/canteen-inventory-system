# Five-minute demonstration

Start the app and keep its server terminal open. Use another PowerShell terminal for these API calls, or perform the same workflow in the browser. The following creates a new demo ingredient each time, so it never resets existing stock.

## 1. Get the business date and create an ingredient

```powershell
$base = 'http://127.0.0.1:8000'
$config = Invoke-RestMethod "$base/api/config"
$today = [datetime]::ParseExact($config.business_date, 'yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture)
$suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)

$riceBody = @{
    name = "Demo Rice $suffix"
    unit = 'kg'
    low_stock_threshold = '5.000'
} | ConvertTo-Json
$rice = Invoke-RestMethod "$base/api/ingredients" -Method Post -ContentType 'application/json' -Body $riceBody
$rice
```

## 2. Receive two batches

```powershell
$aBody = @{
    ingredient_id = $rice.id
    reference = "RICE-A-$suffix"
    quantity = '10.000'
    received_date = $today.ToString('yyyy-MM-dd')
    expiry_date = $today.AddDays(2).ToString('yyyy-MM-dd')
} | ConvertTo-Json
$a = Invoke-RestMethod "$base/api/batches" -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key' = [guid]::NewGuid().ToString()} -Body $aBody

$bBody = @{
    ingredient_id = $rice.id
    reference = "RICE-B-$suffix"
    quantity = '15.000'
    received_date = $today.ToString('yyyy-MM-dd')
    expiry_date = $today.AddDays(7).ToString('yyyy-MM-dd')
} | ConvertTo-Json
$b = Invoke-RestMethod "$base/api/batches" -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key' = [guid]::NewGuid().ToString()} -Body $bBody
```

The ingredient now has **25 kg** usable stock.

## 3. Consume with FEFO and repeat the request

```powershell
$consumeKey = [guid]::NewGuid().ToString()
$consumeBody = @{ingredient_id = $rice.id; quantity = '12.000'; note = 'Lunch preparation'} | ConvertTo-Json
$first = Invoke-RestMethod "$base/api/consumptions" -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key' = $consumeKey} -Body $consumeBody
$first.allocations | Format-Table

$retry = Invoke-RestMethod "$base/api/consumptions" -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key' = $consumeKey} -Body $consumeBody
$first.operation_id -eq $retry.operation_id
Invoke-RestMethod "$base/api/ingredients/$($rice.id)"
```

Expected: allocation uses **10 kg from A and 2 kg from B**. The equality prints `True`. Remaining usable stock is **13 kg**, including after the retry. The history contains only the original consumption allocation movements.

## 4. Record wastage and reconcile

```powershell
$wasteBody = @{batch_id = $b.id; quantity = '1.000'; reason = 'Spilled during handling'} | ConvertTo-Json
Invoke-RestMethod "$base/api/wastages" -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key' = [guid]::NewGuid().ToString()} -Body $wasteBody

$countBody = @{batch_id = $b.id; counted_quantity = '11.500'; reason = 'Physical closing count'} | ConvertTo-Json
$preview = Invoke-RestMethod "$base/api/reconciliations" -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key' = [guid]::NewGuid().ToString()} -Body $countBody
$preview
```

The expected quantity is **12 kg**, the physical count is **11.5 kg**, and the discrepancy is **−0.5 kg**. Review the response before confirming:

```powershell
Invoke-RestMethod "$base/api/reconciliations/$($preview.id)/confirm" -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key' = [guid]::NewGuid().ToString()} -Body '{}'
Invoke-RestMethod "$base/api/movements?ingredient_id=$($rice.id)&page_size=100"
```

History should show an adjustment of **−0.5 kg** alongside the previous receipts, consumption, and wastage.

## 5. Show low stock and failed consumption

```powershell
$lowBody = @{ingredient_id = $rice.id; quantity = '7.000'; note = 'Dinner preparation'} | ConvertTo-Json
Invoke-RestMethod "$base/api/consumptions" -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key' = [guid]::NewGuid().ToString()} -Body $lowBody
Invoke-RestMethod "$base/api/ingredients/$($rice.id)"
```

The balance is **4.5 kg**, below the 5 kg threshold. Refresh the browser to show the low-stock alert and remaining batch's upcoming expiry.

```powershell
$tooMuch = @{ingredient_id = $rice.id; quantity = '8.000'; note = 'Expected failure'} | ConvertTo-Json
try {
    Invoke-RestMethod "$base/api/consumptions" -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key' = [guid]::NewGuid().ToString()} -Body $tooMuch
} catch {
    Write-Host 'Expected conflict: not enough usable stock.'
    Write-Host $_.ErrorDetails.Message
}
Invoke-RestMethod "$base/api/ingredients/$($rice.id)"
```

Expected: a 409 response and unchanged stock of **4.5 kg**. An unrelated error should be investigated rather than counted as a passing demonstration.

## Additional checks to show an interviewer

- Create a preview, record another movement on its batch, then confirm the old preview. It must return a stale-count conflict; create a fresh preview after review.
- Reuse `$consumeKey` with a changed quantity. It must conflict, rather than execute a second operation.
- Receive an already expired batch using a historical receipt date. It increases physical stock but not usable stock; it can be wasted, not consumed.
- Restart the application and show that the same records remain.
- Open `/docs` and demonstrate the same workflows without the browser interface.

Explain what is happening: the API enforces the rules, PostgreSQL records immutable stock movements, FEFO chooses batch allocations, and transactions keep all related writes together.
