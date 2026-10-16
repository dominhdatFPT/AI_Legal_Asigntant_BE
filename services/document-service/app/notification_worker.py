"""Opt-in outbox dispatcher. Session lock across HTTP; no long SQL transaction."""
import argparse
import logging
import time

from app.core.config import Settings, require_session_database
from app.repositories.database import Database
from app.services.notification_delivery import NotificationDelivery, DeliveryError


def event_body(row):
    return {'event_id': str(row['event_id']), 'event_type': row['event_type'],
            'source_service': 'document-service', 'occurred_at': row['occurred_at'].isoformat(),
            'recipient_user_ids': [str(row['recipient_user_id'])], 'resource_type': 'document',
            'resource_id': str(row['document_id']), 'payload': {}}


class Dispatcher:
    def __init__(self, database, delivery):
        self.database, self.delivery = database, delivery

    def run_once(self):
        with self.database.connection() as conn:
            candidates = conn.execute("SELECT event_id FROM document_notification_outbox "
                "WHERE status='pending' AND next_attempt_at<=now() ORDER BY next_attempt_at,event_id LIMIT 100").fetchall()
            conn.commit()
            for candidate in candidates:
                lock = 'document-outbox:' + str(candidate['event_id'])
                acquired = conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked', (lock,)).fetchone()['locked']
                conn.commit()
                if not acquired:
                    continue
                try:
                    row = conn.execute("SELECT * FROM document_notification_outbox WHERE event_id=%s "
                        "AND status='pending' AND next_attempt_at<=now()", (candidate['event_id'],)).fetchone()
                    conn.commit()
                    if row is None:
                        continue
                    status, code = 'sent', None
                    try:
                        self.delivery.send(event_body(row))
                    except DeliveryError as exc:
                        status = 'pending' if exc.retryable and row['attempts'] < 11 else 'failed'
                        code = exc.code
                    except Exception:
                        # Ambiguous delivery: retain identical body/ID, never mark sent.
                        status = 'pending' if row['attempts'] < 11 else 'failed'
                        code = 'NOTIFICATION_UNAVAILABLE'
                    conn.execute("UPDATE document_notification_outbox SET status=%s,attempts=attempts+1,"
                        "last_error_code=%s,next_attempt_at=now()+make_interval(secs=>%s),"
                        "sent_at=CASE WHEN %s='sent' THEN now() ELSE NULL END "
                        "WHERE event_id=%s AND status='pending' AND attempts=%s",
                        (status, code, min(3600, 10 * 2**row['attempts']), status, row['event_id'], row['attempts']))
                    conn.commit()
                    return True
                finally:
                    conn.rollback()
                    conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (lock,))
                    conn.commit()
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        require_session_database(settings)
        dispatcher = Dispatcher(Database(settings), NotificationDelivery.from_env())
    except Exception:
        print('Notification worker disabled/config invalid; credentials omitted.')
        return 1
    while True:
        try:
            worked = dispatcher.run_once()
        except Exception:
            logging.getLogger('document-outbox').warning('Outbox cycle unavailable; content/credentials omitted.')
            worked = False
        if args.once:
            return 0 if worked else 2
        if not worked:
            time.sleep(2)


if __name__ == '__main__':
    raise SystemExit(main())
