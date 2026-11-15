import duckdb

g = ".cache/catalogue/shard_*.parquet"
print(duckdb.sql(f"select count(*) n, min(t_start), max(t_end) from read_parquet('{g}')"))
print(duckdb.sql(f"select release, count(*) from read_parquet('{g}') group by 1 order by 1"))
print(duckdb.sql(f"select detector, count(*) from read_parquet('{g}') group by 1 order by 1"))
