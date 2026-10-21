-- One-time cleanup of blank Screener values already stored in Supabase.
-- Run in the Supabase SQL editor. Safe to re-run.
--
-- Why: Screener prints only the unit ("₹ Cr.", "₹", "%", "₹ /", "- close price")
-- when a figure is missing. Earlier loader versions stored those cells as rows
-- with raw_value = '₹ Cr.' and value = NULL, and the backend then passed them
-- to the report writer as "verified" data.

begin;

-- 1. See what will be removed (optional).
select 'ratios' as tbl, count(*) from ratios where value is null
union all select 'financials', count(*) from financials where value is null
union all select 'growth_cagr', count(*) from growth_cagr where value is null
union all select 'shareholding', count(*) from shareholding where value is null;

-- 2. Remove rows that hold no number.
delete from ratios       where value is null;
delete from financials   where value is null;
delete from growth_cagr  where value is null;
delete from shareholding where value is null;

-- 3. Unit-only text in company fields.
update companies set price_date_label = null
 where price_date_label is not null and price_date_label !~ '[0-9]';
update companies set raw_info = raw_info - 'Price Date'
 where raw_info ? 'Price Date' and coalesce(raw_info->>'Price Date', '') !~ '[0-9]';

-- 4. Guard rails: from now on a row without a number cannot be inserted.
alter table ratios       alter column value set not null;
alter table financials   alter column value set not null;
alter table growth_cagr  alter column value set not null;
alter table shareholding alter column value set not null;

commit;

-- 5. Companies that have no financial figures at all — usually a consolidated
--    Screener page for a company that reports standalone only (e.g. VESUVIUS).
--    Re-scrape these with the updated screener_all_stock_data.py (it now falls
--    back to the standalone page) and reload them.
select c.ticker, c.name, c.company_url
  from companies c
 where not exists (select 1 from financials f where f.company_id = c.id)
   and not exists (select 1 from ratios r where r.company_id = c.id)
 order by c.ticker;
