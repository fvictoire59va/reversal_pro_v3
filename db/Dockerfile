FROM postgres:16-alpine

# Copy all SQL scripts
COPY db/init.sql /scripts/01_init.sql
COPY db/migrate_add_agent_balance.sql /scripts/02_migrate_add_agent_balance.sql
COPY db/migrate_add_agent_params.sql /scripts/03_migrate_add_agent_params.sql
COPY db/migrate_add_invested_eur.sql /scripts/04_migrate_add_invested_eur.sql
COPY db/migrate_add_signal_detected_at.sql /scripts/05_migrate_add_signal_detected_at.sql
COPY db/migrate_add_signal_tracking.sql /scripts/06_migrate_add_signal_tracking.sql
COPY db/migrate_add_unrealized_pnl.sql /scripts/07_migrate_add_unrealized_pnl.sql
COPY db/migrate_add_partial_tp_breakeven.sql /scripts/08_migrate_add_partial_tp_breakeven.sql
COPY db/migrate_add_trailing_stop.sql /scripts/09_migrate_add_trailing_stop.sql

COPY db/run-migrations.sh /scripts/run-migrations.sh
RUN chmod +x /scripts/run-migrations.sh

CMD ["/scripts/run-migrations.sh"]
