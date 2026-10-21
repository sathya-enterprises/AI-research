-- Read-only diagnostics: where do "blank" Screener values live in Supabase?
-- Run each block in the Supabase SQL editor and look at the counts.

-- A. NaN / Infinity stored as numbers (NOT caught by "value is null").
select 'ratios' as tbl, count(*) from ratios where value = 'NaN' or value in ('Infinity', '-Infinity')
union all select 'financials',   count(*) from financials   where value = 'NaN' or value in ('Infinity', '-Infinity')
union all select 'growth_cagr',  count(*) from growth_cagr  where value = 'NaN' or value in ('Infinity', '-Infinity')
union all select 'shareholding', count(*) from shareholding where value = 'NaN' or value in ('Infinity', '-Infinity');

-- B. Rows whose stored text has no digit at all ("₹ Cr.", "%", "-").
select 'ratios' as tbl, count(*) from ratios where raw_value is not null and raw_value !~ '[0-9]'
union all select 'financials',   count(*) from financials   where raw_value is not null and raw_value !~ '[0-9]'
union all select 'growth_cagr',  count(*) from growth_cagr  where raw_value is not null and raw_value !~ '[0-9]'
union all select 'shareholding', count(*) from shareholding where raw_value is not null and raw_value !~ '[0-9]';

-- C. Blank company headline fields (market cap / P/E / price).
select count(*) filter (where market_cap is null)    as no_market_cap,
       count(*) filter (where pe_ratio is null)      as no_pe,
       count(*) filter (where current_price is null) as no_price,
       count(*) filter (where price_date_label !~ '[0-9]') as placeholder_date,
       count(*) as companies
  from companies;

-- D. Companies that exist but have no figures at all (empty Screener page).
select c.ticker, c.name, c.company_url
  from companies c
 where not exists (select 1 from financials f where f.company_id = c.id)
   and not exists (select 1 from ratios r where r.company_id = c.id)
 order by c.ticker;

-- E. Per-company coverage for the workbooks you checked.
select c.ticker, c.market_cap, c.pe_ratio, c.price_date_label,
       (select count(*) from ratios r       where r.company_id = c.id) as ratios,
       (select count(*) from financials f   where f.company_id = c.id) as financials,
       (select count(*) from growth_cagr g  where g.company_id = c.id) as growth,
       (select count(*) from shareholding s where s.company_id = c.id) as shareholding
  from companies c
 where c.ticker in ('VESUVIUS', 'VERTOZ', 'VHLTD', 'VETO', 'VGINFOTECH', 'VERTIS')
 order by c.ticker;
