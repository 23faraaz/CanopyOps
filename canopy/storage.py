import json
import os
import sqlite3
from decimal import Decimal
from canopy.domain import STATIONS, advance

class LocalStore:
    def __init__(self):
        self.path = os.getenv('DATABASE_PATH', 'canopy.db')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, station TEXT, observed TEXT, payload TEXT);
                CREATE INDEX IF NOT EXISTS event_station ON events(station, observed);
                CREATE TABLE IF NOT EXISTS states (id TEXT PRIMARY KEY, payload TEXT);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def process(self, reading):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM events WHERE id=?', (reading.event_id,)).fetchone():
                return False
            row = db.execute('SELECT payload FROM states WHERE id=?', (reading.station_id,)).fetchone()
            state = advance(json.loads(row[0]) if row else None, reading)
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (reading.event_id, reading.station_id,
                       reading.observed_at.isoformat(), reading.model_dump_json()))
            db.execute('INSERT OR REPLACE INTO states VALUES (?,?)', (reading.station_id, json.dumps(state)))
        return True

    def states(self):
        with self.connect() as db:
            return {row[0]: json.loads(row[1]) for row in db.execute('SELECT id,payload FROM states')}

    def history(self, station):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM events WHERE station=? ORDER BY observed DESC LIMIT 60', (station,))
            return list(reversed([json.loads(r[0]) for r in rows]))

class AwsStore:
    '''Table contract: string partition key pk, string sort key sk. No secondary indexes required.'''
    def __init__(self):
        import boto3
        self.table = boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])
        self.client = self.table.meta.client

    def process(self, reading):
        from boto3.dynamodb.types import TypeSerializer
        from botocore.exceptions import ClientError
        serializer = TypeSerializer()
        def encode(item):
            return {k: serializer.serialize(v) for k, v in json.loads(json.dumps(item), parse_float=Decimal).items()}
        key = {'pk': 'STATION#' + reading.station_id, 'sk': 'STATE'}
        marker = {'pk': 'EVENT#' + reading.event_id, 'sk': 'EVENT'}
        for _ in range(5):
            if self.table.get_item(Key=marker, ConsistentRead=True).get('Item'):
                return False
            previous = self.table.get_item(Key=key, ConsistentRead=True).get('Item')
            version = int(previous['version']) if previous else 0
            state = advance(json.loads(previous['payload']) if previous else None, reading)
            condition = 'version = :v' if previous else 'attribute_not_exists(pk)'
            put = {'TableName': self.table.name, 'Item': encode({**key, 'version': version + 1, 'payload': json.dumps(state)}),
                   'ConditionExpression': condition}
            if previous:
                put['ExpressionAttributeValues'] = encode({':v': version})
            try:
                # Use a low-level client: the resource client would serialize these values twice.
                import boto3
                boto3.client('dynamodb').transact_write_items(TransactItems=[
                    {'Put': {'TableName': self.table.name, 'Item': encode(marker), 'ConditionExpression': 'attribute_not_exists(pk)'}},
                    {'Put': put},
                    {'Put': {'TableName': self.table.name, 'Item': encode({'pk': key['pk'], 'sk': 'READING#' + reading.observed_at.isoformat() + '#' + reading.event_id, 'payload': reading.model_dump_json()})}},
                ])
                return True
            except ClientError as exc:
                if exc.response['Error']['Code'] != 'TransactionCanceledException':
                    raise
        raise RuntimeError('Concurrent updates exceeded retry budget; leave message for retry')

    def states(self):
        result = {}
        for station in STATIONS:
            item = self.table.get_item(Key={'pk': 'STATION#' + station, 'sk': 'STATE'}, ConsistentRead=True).get('Item')
            if item:
                result[station] = json.loads(item['payload'])
        return result

    def history(self, station):
        from boto3.dynamodb.conditions import Key
        rows = self.table.query(KeyConditionExpression=Key('pk').eq('STATION#' + station) & Key('sk').begins_with('READING#'),
                                ScanIndexForward=False, Limit=60, ConsistentRead=True)['Items']
        return list(reversed([json.loads(r['payload']) for r in rows]))

def store():
    backend = os.getenv('BACKEND', 'local')
    if backend not in ('local', 'aws'):
        raise ValueError('BACKEND must be local or aws')
    return AwsStore() if backend == 'aws' else LocalStore()
