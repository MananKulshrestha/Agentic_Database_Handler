CREATE TABLE customers (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email text NOT NULL UNIQUE,
  full_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE orders (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  customer_id bigint NOT NULL REFERENCES customers(id),
  placed_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'new'
);
CREATE TABLE order_items (
  order_id bigint NOT NULL REFERENCES orders(id),
  product_id bigint NOT NULL,
  product_name text NOT NULL,
  quantity integer NOT NULL CHECK (quantity > 0),
  sale_price numeric(12,2) NOT NULL,
  PRIMARY KEY (order_id, product_id)
);
CREATE TABLE event_log (
  created_at timestamp,
  payload text
);
COMMENT ON TABLE order_items IS 'Product name and price are snapshots recorded at purchase time.';
INSERT INTO customers(email, full_name) VALUES ('ada@example.test', 'Ada'), ('lin@example.test', 'Lin');
INSERT INTO orders(customer_id, status) VALUES (1, 'paid'), (2, 'new');
INSERT INTO order_items(order_id, product_id, product_name, quantity, sale_price)
VALUES (1, 10, 'Notebook', 2, 12.50);
CREATE ROLE demo_read LOGIN PASSWORD 'demo_read';
GRANT CONNECT ON DATABASE demo TO demo_read;
GRANT USAGE ON SCHEMA public TO demo_read;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO demo_read;
