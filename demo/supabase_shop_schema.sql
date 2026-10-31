-- Synthetic shop schema for the Agentic Database Handler college project.
-- Only demo_ objects are created. The intentionally rough import table gives
-- the schema analyzer a missing-primary-key example.

create table public.demo_customers (
    id bigint generated always as identity primary key,
    full_name text not null,
    email text not null unique,
    city text not null,
    created_at timestamptz not null default now()
);

create table public.demo_categories (
    id bigint generated always as identity primary key,
    name text not null unique,
    description text
);

create table public.demo_products (
    id bigint generated always as identity primary key,
    category_id bigint not null references public.demo_categories(id),
    sku text not null unique,
    name text not null,
    unit_price numeric(10, 2) not null check (unit_price >= 0),
    stock_quantity integer not null default 0 check (stock_quantity >= 0),
    active boolean not null default true,
    created_at timestamptz not null default now()
);
create index demo_products_category_id_idx on public.demo_products(category_id);

create table public.demo_orders (
    id bigint generated always as identity primary key,
    order_number text not null unique,
    customer_id bigint not null references public.demo_customers(id),
    placed_at timestamptz not null default now(),
    status text not null check (status in ('pending', 'paid', 'shipped', 'cancelled')),
    shipping_city text not null,
    note text
);
create index demo_orders_customer_id_idx on public.demo_orders(customer_id);
create index demo_orders_placed_at_idx on public.demo_orders(placed_at);

create table public.demo_order_items (
    order_id bigint not null references public.demo_orders(id) on delete cascade,
    product_id bigint not null references public.demo_products(id),
    quantity integer not null check (quantity > 0),
    unit_price numeric(10, 2) not null check (unit_price >= 0),
    primary key (order_id, product_id)
);
create index demo_order_items_product_id_idx on public.demo_order_items(product_id);

create table public.demo_payments (
    id bigint generated always as identity primary key,
    order_id bigint not null unique references public.demo_orders(id),
    amount numeric(10, 2) not null check (amount > 0),
    method text not null check (method in ('card', 'upi', 'cash')),
    status text not null check (status in ('completed', 'refunded', 'failed')),
    paid_at timestamptz not null
);

create table public.demo_shipments (
    id bigint generated always as identity primary key,
    order_id bigint not null unique references public.demo_orders(id),
    carrier text not null,
    tracking_number text not null unique,
    shipped_at timestamptz not null,
    delivered_at timestamptz,
    check (delivered_at is null or delivered_at >= shipped_at)
);

create table public.demo_legacy_imports (
    source_record text not null,
    contact_name text not null,
    email text,
    city text,
    imported_at timestamptz not null default now()
);

comment on table public.demo_legacy_imports is
    'Synthetic staging data intentionally lacks a primary key for schema-analysis demonstrations.';

-- Public is normally an exposed Supabase schema. The demo tables are private
-- to the dedicated inspection role, with RLS enabled on every new table.
alter table public.demo_customers enable row level security;
alter table public.demo_categories enable row level security;
alter table public.demo_products enable row level security;
alter table public.demo_orders enable row level security;
alter table public.demo_order_items enable row level security;
alter table public.demo_payments enable row level security;
alter table public.demo_shipments enable row level security;
alter table public.demo_legacy_imports enable row level security;

revoke all on public.demo_customers, public.demo_categories, public.demo_products,
    public.demo_orders, public.demo_order_items, public.demo_payments,
    public.demo_shipments, public.demo_legacy_imports
from public, anon, authenticated;

grant usage on schema public to agentic_inspector;
grant select on public.demo_customers, public.demo_categories, public.demo_products,
    public.demo_orders, public.demo_order_items, public.demo_payments,
    public.demo_shipments, public.demo_legacy_imports
to agentic_inspector;

create policy demo_customers_inspector_read on public.demo_customers
    for select to agentic_inspector using (true);
create policy demo_categories_inspector_read on public.demo_categories
    for select to agentic_inspector using (true);
create policy demo_products_inspector_read on public.demo_products
    for select to agentic_inspector using (true);
create policy demo_orders_inspector_read on public.demo_orders
    for select to agentic_inspector using (true);
create policy demo_order_items_inspector_read on public.demo_order_items
    for select to agentic_inspector using (true);
create policy demo_payments_inspector_read on public.demo_payments
    for select to agentic_inspector using (true);
create policy demo_shipments_inspector_read on public.demo_shipments
    for select to agentic_inspector using (true);
create policy demo_legacy_imports_inspector_read on public.demo_legacy_imports
    for select to agentic_inspector using (true);

create view public.demo_order_totals with (security_invoker = true) as
select
    o.id as order_id,
    o.order_number,
    c.full_name as customer_name,
    o.status,
    o.placed_at,
    sum(oi.quantity * oi.unit_price)::numeric(12, 2) as order_total
from public.demo_orders o
join public.demo_customers c on c.id = o.customer_id
join public.demo_order_items oi on oi.order_id = o.id
group by o.id, o.order_number, c.full_name, o.status, o.placed_at;

revoke all on public.demo_order_totals from public, anon, authenticated;
grant select on public.demo_order_totals to agentic_inspector;
