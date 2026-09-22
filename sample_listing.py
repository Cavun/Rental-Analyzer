"""
sample_listing.py

A realistic (fabricated) flexmls-shaped listing-detail page so `python3 main.py`
runs end to end with no setup beyond `pip install -r requirements.txt`.

The structure mirrors a real Spark Platform page: the core fields live in the
JSON blob on the `data-map--ldp-listing` attribute, everything else lives in
`.listing-detail-field-line` / `.listing-detail-field-label` pairs.
"""

SAMPLE_LISTING_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>1428 Maplewood Street SE, Grand Rapids, MI 49506 | MLS# 26051188</title></head>
<body>
  <div class="listing-detail-page"
       data-map--ldp-listing="{&quot;ListingKey&quot;: &quot;20260915230855307019000000&quot;, &quot;MlsId&quot;: &quot;20140402194227539412000000&quot;, &quot;BedsTotal&quot;: &quot;3&quot;, &quot;BathsTotal&quot;: &quot;2.0&quot;, &quot;StreetNumber&quot;: &quot;1428&quot;, &quot;StreetName&quot;: &quot;Maplewood&quot;, &quot;StreetSuffix&quot;: &quot;Street&quot;, &quot;StreetDirSuffix&quot;: &quot;SE&quot;, &quot;StreetAddress&quot;: &quot;1428 Maplewood Street SE&quot;, &quot;City&quot;: &quot;Grand Rapids&quot;, &quot;StateOrProvince&quot;: &quot;MI&quot;, &quot;PostalCode&quot;: &quot;49506&quot;, &quot;ListingId&quot;: &quot;26051188&quot;, &quot;CurrentPrice&quot;: 239900.0, &quot;ListPrice&quot;: 239900.0, &quot;MlsStatus&quot;: &quot;Active&quot;, &quot;Latitude&quot;: 42.93871, &quot;Longitude&quot;: -85.62214, &quot;StandardFields&quot;: {&quot;Latitude&quot;: 42.93871, &quot;Longitude&quot;: -85.62214, &quot;PropertyClass&quot;: &quot;Residential&quot;, &quot;PropertyType&quot;: &quot;A&quot;, &quot;StandardStatus&quot;: &quot;Active&quot;, &quot;CurrentPrice&quot;: 239900.0}}">
    <header class="ldp-header">
      <h1 class="listing-address">1428 Maplewood Street SE</h1>
      <div class="listing-price">$239,900</div>
      <div class="listing-summary">3 Beds | 2 Baths | 1,392 SqFt | Active</div>
    </header>
    <section class="listing-detail-fields">
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">DOM</div><div class="listing-detail-field-value">12</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">County</div><div class="listing-detail-field-value">Kent</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Cross Streets</div><div class="listing-detail-field-value">Eastern and Alger</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Property Sub-Type</div><div class="listing-detail-field-value">Single Family Residence</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Municipality</div><div class="listing-detail-field-value">City of Grand Rapids</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">List Price/SqFt</div><div class="listing-detail-field-value">172.35</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Auction or For Sale</div><div class="listing-detail-field-value">For Sale</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Current Price</div><div class="listing-detail-field-value">$239,900</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Ownership Type</div><div class="listing-detail-field-value">Private Owned</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">New Construction</div><div class="listing-detail-field-value">No</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Lot Measurement</div><div class="listing-detail-field-value">Acres</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Lot Acres</div><div class="listing-detail-field-value">0.14</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Lot Square Footage</div><div class="listing-detail-field-value">6,098</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Lot Dimensions</div><div class="listing-detail-field-value">44x139</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Year Built</div><div class="listing-detail-field-value">1948</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Garage Y/N</div><div class="listing-detail-field-value">Yes</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Garage Spaces</div><div class="listing-detail-field-value">2</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Fireplace</div><div class="listing-detail-field-value">Yes</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Income Property</div><div class="listing-detail-field-value">No</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Total Rooms AG</div><div class="listing-detail-field-value">6</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Total Fin SqFt All Levels</div><div class="listing-detail-field-value">1,392</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">SqFt Above Grade</div><div class="listing-detail-field-value">1,392</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Below Grade Finished SqFt</div><div class="listing-detail-field-value">0</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Total Bedrooms</div><div class="listing-detail-field-value">3</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Full Baths</div><div class="listing-detail-field-value">1</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Half Baths</div><div class="listing-detail-field-value">1</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Total Baths</div><div class="listing-detail-field-value">2</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Stories</div><div class="listing-detail-field-value">2</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Basement</div><div class="listing-detail-field-value">Yes</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">School District</div><div class="listing-detail-field-value">Grand Rapids</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Association YN</div><div class="listing-detail-field-value">No</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Waterfront</div><div class="listing-detail-field-value">No</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Tax ID #</div><div class="listing-detail-field-value">41-14-32-201-009</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Taxable Value</div><div class="listing-detail-field-value">58,420</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">SEV</div><div class="listing-detail-field-value">94,700</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">For Tax Year</div><div class="listing-detail-field-value">2026</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Seller&#x27;s Annual Property Tax</div><div class="listing-detail-field-value">2,184</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Tax Year</div><div class="listing-detail-field-value">2026</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Homestead %</div><div class="listing-detail-field-value">100</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Spec Assessment &amp; Type</div><div class="listing-detail-field-value">none known</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Zoning</div><div class="listing-detail-field-value">LDR</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Public Remarks</div><div class="listing-detail-field-value">Solid three bedroom, one and a half bath home in a steady east side rental pocket. Vinyl windows, tear-off roof in 2019, forced air furnace 2016. Hardwood under carpet on the main level. Detached two stall garage off the alley. Full unfinished basement with laundry. Fenced yard. Currently owner occupied and move-in ready.</div></div>
        <div class="listing-detail-field-line"><div class="listing-detail-field-label">Legal</div><div class="listing-detail-field-value">LOT 9 * MAPLEWOOD ADDITION</div></div>
    </section>
  </div>
</body>
</html>
"""
