-- Fictional records for public.demo_* tables. Safe to run again: natural
-- business keys prevent duplicates without relying on generated IDs.

insert into public.demo_customers (full_name, email, city, created_at) values
    ('Asha Rao', 'asha.rao@example.test', 'Bengaluru', '2026-09-20 09:00:00+00'),
    ('Dev Mehta', 'dev.mehta@example.test', 'Pune', '2026-09-21 10:30:00+00'),
    ('Mira Sen', 'mira.sen@example.test', 'Kolkata', '2026-09-22 11:00:00+00'),
    ('Kabir Shah', 'kabir.shah@example.test', 'Mumbai', '2026-09-23 08:20:00+00'),
    ('Neha Iyer', 'neha.iyer@example.test', 'Chennai', '2026-09-24 13:40:00+00'),
    ('Rohan Das', 'rohan.das@example.test', 'Hyderabad', '2026-09-25 15:10:00+00')
on conflict (email) do nothing;

insert into public.demo_categories (name, description) values
    ('Electronics', 'Small gadgets and accessories'),
    ('Home', 'Everyday home and desk items'),
    ('Stationery', 'Writing and paper supplies')
on conflict (name) do nothing;

insert into public.demo_products
    (category_id, sku, name, unit_price, stock_quantity, active)
select c.id, p.sku, p.name, p.unit_price, p.stock_quantity, true
from (values
    ('Home', 'SKU-LAMP', 'Desk lamp', 1299.00, 24),
    ('Stationery', 'SKU-NOTE', 'Notebook', 249.00, 120),
    ('Stationery', 'SKU-PEN', 'Gel pen pack', 99.00, 180),
    ('Electronics', 'SKU-HEAD', 'Headphones', 1999.00, 18),
    ('Home', 'SKU-MUG', 'Ceramic mug', 399.00, 62),
    ('Home', 'SKU-CHAIR', 'Desk chair', 4499.00, 11),
    ('Electronics', 'SKU-USB', 'USB-C hub', 599.00, 34),
    ('Home', 'SKU-BOTTLE', 'Steel bottle', 699.00, 47)
) as p(category_name, sku, name, unit_price, stock_quantity)
join public.demo_categories c on c.name = p.category_name
where true
on conflict (sku) do nothing;

insert into public.demo_orders
    (order_number, customer_id, placed_at, status, shipping_city, note)
select o.order_number, c.id, o.placed_at, o.status, o.shipping_city, o.note
from (values
    ('ORD-1001', 'asha.rao@example.test', '2026-10-01 10:00:00+00'::timestamptz, 'paid', 'Bengaluru', null),
    ('ORD-1002', 'dev.mehta@example.test', '2026-10-01 11:30:00+00'::timestamptz, 'paid', 'Pune', 'Gift wrap'),
    ('ORD-1003', 'mira.sen@example.test', '2026-10-02 09:15:00+00'::timestamptz, 'shipped', 'Kolkata', null),
    ('ORD-1004', 'kabir.shah@example.test', '2026-10-02 14:45:00+00'::timestamptz, 'pending', 'Mumbai', null),
    ('ORD-1005', 'neha.iyer@example.test', '2026-10-03 12:10:00+00'::timestamptz, 'shipped', 'Chennai', null),
    ('ORD-1006', 'rohan.das@example.test', '2026-10-03 16:30:00+00'::timestamptz, 'cancelled', 'Hyderabad', 'Cancelled before payment'),
    ('ORD-1007', 'asha.rao@example.test', '2026-10-04 08:50:00+00'::timestamptz, 'paid', 'Bengaluru', null),
    ('ORD-1008', 'dev.mehta@example.test', '2026-10-04 18:20:00+00'::timestamptz, 'pending', 'Pune', null)
) as o(order_number, customer_email, placed_at, status, shipping_city, note)
join public.demo_customers c on c.email = o.customer_email
where true
on conflict (order_number) do nothing;

insert into public.demo_order_items
    (order_id, product_id, quantity, unit_price)
select o.id, p.id, i.quantity, i.unit_price
from (values
    ('ORD-1001', 'SKU-LAMP', 1, 1299.00),
    ('ORD-1001', 'SKU-NOTE', 2, 249.00),
    ('ORD-1002', 'SKU-HEAD', 1, 1999.00),
    ('ORD-1002', 'SKU-USB', 1, 599.00),
    ('ORD-1003', 'SKU-MUG', 2, 399.00),
    ('ORD-1003', 'SKU-BOTTLE', 1, 699.00),
    ('ORD-1004', 'SKU-CHAIR', 1, 4499.00),
    ('ORD-1005', 'SKU-PEN', 3, 99.00),
    ('ORD-1005', 'SKU-NOTE', 1, 249.00),
    ('ORD-1006', 'SKU-LAMP', 1, 1299.00),
    ('ORD-1007', 'SKU-BOTTLE', 2, 699.00),
    ('ORD-1007', 'SKU-USB', 1, 599.00),
    ('ORD-1008', 'SKU-MUG', 1, 399.00)
) as i(order_number, sku, quantity, unit_price)
join public.demo_orders o on o.order_number = i.order_number
join public.demo_products p on p.sku = i.sku
where true
on conflict (order_id, product_id) do nothing;

insert into public.demo_payments (order_id, amount, method, status, paid_at)
select o.id, p.amount, p.method, 'completed', p.paid_at
from (values
    ('ORD-1001', 1797.00, 'upi', '2026-10-01 10:02:00+00'::timestamptz),
    ('ORD-1002', 2598.00, 'card', '2026-10-01 11:34:00+00'::timestamptz),
    ('ORD-1003', 1497.00, 'upi', '2026-10-02 09:18:00+00'::timestamptz),
    ('ORD-1005', 546.00, 'card', '2026-10-03 12:13:00+00'::timestamptz),
    ('ORD-1007', 1997.00, 'upi', '2026-10-04 08:54:00+00'::timestamptz)
) as p(order_number, amount, method, paid_at)
join public.demo_orders o on o.order_number = p.order_number
where true
on conflict (order_id) do nothing;

insert into public.demo_shipments
    (order_id, carrier, tracking_number, shipped_at, delivered_at)
select o.id, s.carrier, s.tracking_number, s.shipped_at, s.delivered_at
from (values
    ('ORD-1003', 'Demo Parcel', 'DEMO-TRACK-1003', '2026-10-03 07:00:00+00'::timestamptz, null::timestamptz),
    ('ORD-1005', 'Demo Parcel', 'DEMO-TRACK-1005', '2026-10-04 06:30:00+00'::timestamptz, '2026-10-06 13:00:00+00'::timestamptz)
) as s(order_number, carrier, tracking_number, shipped_at, delivered_at)
join public.demo_orders o on o.order_number = s.order_number
where true
on conflict (order_id) do nothing;

insert into public.demo_legacy_imports
    (source_record, contact_name, email, city, imported_at)
select i.source_record, i.contact_name, i.email, i.city, i.imported_at
from (values
    ('CSV-001', 'Asha Rao', 'asha.rao@example.test', 'Bengaluru', '2026-10-05 10:00:00+00'::timestamptz),
    ('CSV-002', 'A. Rao', 'asha.rao@example.test', 'Bangalore', '2026-10-05 10:00:00+00'::timestamptz),
    ('CSV-003', 'Mira Sen', 'mira.sen@example.test', 'Kolkata', '2026-10-05 10:00:00+00'::timestamptz),
    ('CSV-004', 'Unknown contact', null, null, '2026-10-05 10:00:00+00'::timestamptz)
) as i(source_record, contact_name, email, city, imported_at)
where not exists (
    select 1 from public.demo_legacy_imports existing
    where existing.source_record = i.source_record
);
