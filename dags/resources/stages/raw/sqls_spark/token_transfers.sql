CREATE TABLE IF NOT EXISTS {{database_temp}}.{{table}}
(
    token_address    STRING,
    from_address     STRING,
    to_address       STRING,
    value            DECIMAL(38, 0),
    transaction_hash STRING,
    log_index        BIGINT,
    block_number     BIGINT
) USING json
OPTIONS (
    path "{{file_path}}"
);