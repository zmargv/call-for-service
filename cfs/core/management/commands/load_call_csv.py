import datetime as dt
import pandas as pd
import math
from django.core.management import call_command

from django.core.management.base import BaseCommand

# File fields
# - Internal ID
# - Time Received
# - Time Dispatched
# - Time Arrived
# - Time Closed
# - Street Address
# - City
# - Zip
# - Latitude
# - Longitude
# - Priority
# - Source
# - District
# - Beat
# - Primary Unit
# - Department
# - Nature Code
# - Nature Text
# - Close Code
# - Close Text
from django.db import IntegrityError

from core.models import (District, Beat, Priority, Nature, CallSource,
                         CloseCode, Call, City, Agency, Department,
                         CallUnit)


def isnan(x):
    return x is None or (type(x) == float and math.isnan(x))


def safe_int(x):
    if isnan(x):
        return None
    return int(x)


def safe_float(x):
    if isnan(x):
        return None
    return float(x)


def safe_datetime(x):
    if x is pd.NaT:
        return None
    return x


def safe_zip(zip):
    if isnan(zip):
        return None
    return zip.strip()[:5]


def safe_sorted(coll):
    return sorted(x for x in coll if not isnan(x))


def uniq_list_by_key(alist, key):
    """Make a list unique by using a key function over its items."""
    seen = set()
    # set().add() returns None, making this work.
    return [seen.add(key(obj)) or obj for obj in alist if key(obj) not in seen]


class Command(BaseCommand):
    help = "Load call for service data from a CSV."

    def add_arguments(self, parser):
        parser.add_argument('filename', type=str, help='The CSV file to load.')
        parser.add_argument('--reset', default=False, action='store_true',
                            help='Whether to clear the database before loading '
                                 '(defaults to False)')
        parser.add_argument('--agency', type=str,
                            help="The code for the agency the calls belong "
                                 "to. Without this option, they will be "
                                 "assigned to the first agency found.")
        parser.add_argument('--update', default=False, action='store_true',
                            help='Whether to update calls that have '
                                 'previously been saved.')

    def clear_database(self):
        self.log("Clearing database")
        call_command("flush", interactive=False)

    def log(self, message):
        if self.start_time:
            current_time = dt.datetime.now()
            period = current_time - self.start_time
        else:
            period = dt.timedelta(0)
        print("[{:7.2f}] {}".format(period.total_seconds(), message))

    def handle(self, *args, **options):
        self.start_time = dt.datetime.now()

        if options['reset']:
            self.clear_database()

        if options['agency']:
            self.agency = Agency.objects.get(code=options['agency'])
        else:
            self.agency = Agency.objects.first()
            self.log("Using default agency: " + self.agency.code)

        self.batch_size = 2000

        self.log("Loading CSV")
        self.df = pd.read_csv(options['filename'],
                              parse_dates=['Time Received', 'Time Dispatched',
                                           'Time Arrived', 'Time Closed'],
                              dtype={'Internal ID': str, 'District': str,
                                     'Priority': str, 'Nature Code': str,
                                     'Close Code': str, 'Zip': str})

        self.log("CSV loaded")

        creation_methods = [
            ('District', self.create_districts),
            ('Beat', self.create_beats),
            ('Priority', self.create_priorities),
            ('Nature Code', self.create_natures),
            ('Close Code', self.create_close_codes),
            ('Source Code', self.create_sources),
            ('City', self.create_cities),
            ('Department', self.create_departments),
            ('Primary Unit', self.create_primary_units),
        ]

        for col, method in creation_methods:
            if col in self.df:
                method()

        self.create_calls(update=options['update'])

    def update_call(self, call, **kwargs):
        for attr, value in kwargs.items():
            setattr(call, attr, value)
        call.update_derived_fields()
        call.save()

    def create_calls(self, update):
        start = 0
        while start < len(self.df):
            batch = self.df[start:start + self.batch_size]
            calls = []

            for idx, c in batch.iterrows():
                safe_get = lambda col: c[col] if col in c else None

                if Call.objects.filter(pk=c['Internal ID']).count() > 0:
                    if update:
                        call = Call.objects.get(call_id=c['Internal ID'])
                        self.update_call(
                            call,
                            agency=self.agency,
                            time_received=safe_datetime(c['Time Received']),
                            first_unit_dispatch=safe_datetime(
                                c['Time Dispatched']),
                            first_unit_arrive=safe_datetime(c['Time Arrived']),
                            time_closed=safe_datetime(c['Time Closed']),
                            street_address=safe_get('Street Address'),
                            zip_code=safe_zip(safe_get('Zip')),
                            nature_id=safe_int(safe_get('Nature ID')),
                            city_id=safe_int(safe_get('City ID')),
                            priority_id=safe_int(safe_get('Priority ID')),
                            district_id=safe_int(safe_get('District ID')),
                            beat_id=safe_int(safe_get('Beat ID')),
                            call_source_id=safe_int(safe_get('Source ID')),
                            close_code_id=safe_int(safe_get('Close Code ID')),
                            geox=safe_float(c['Longitude']),
                            geoy=safe_float(c['Latitude']))
                    continue

                call = Call(call_id=c['Internal ID'],
                            agency=self.agency,
                            time_received=safe_datetime(c['Time Received']),
                            first_unit_dispatch=safe_datetime(
                                c['Time Dispatched']),
                            first_unit_arrive=safe_datetime(
                                c['Time Arrived']),
                            time_closed=safe_datetime(c['Time Closed']),
                            street_address=safe_get('Street Address'),
                            zip_code=safe_zip(safe_get('Zip')),
                            nature_id=safe_int(safe_get('Nature ID')),
                            city_id=safe_int(safe_get('City ID')),
                            priority_id=safe_int(safe_get('Priority ID')),
                            district_id=safe_int(safe_get('District ID')),
                            beat_id=safe_int(safe_get('Beat ID')),
                            call_source_id=safe_int(safe_get('Source ID')),
                            close_code_id=safe_int(safe_get('Close Code ID')),
                            department_id=safe_int(safe_get('Department ID')),
                            primary_unit_id=safe_int(safe_get('Primary Unit ID')),
                            geox=safe_float(c['Longitude']),
                            geoy=safe_float(c['Latitude']))
                call.update_derived_fields()
                calls.append(call)

            try:
                Call.objects.bulk_create(calls)
                self.log("Call {}-{} created".format(start, start + len(batch)))
                start += self.batch_size
            except IntegrityError as ex:
                # TODO fix call
                self.log("Duplicates found")
                calls = uniq_list_by_key(calls, lambda call: call.call_id)
                Call.objects.bulk_create(calls)
                self.log("Call {}-{} created".format(start, start + len(batch)))
                start += self.batch_size

    def create_beats(self):
        self.log("Creating beats")
        df = self.df

        beat_names = safe_sorted(df['Beat'].unique())
        beats = [Beat.objects.get_or_create(descr=name)[0]
                 for name in beat_names]
        beat_map = {b.descr: b.beat_id for b in beats}
        df['Beat ID'] = df['Beat'].apply(lambda x: beat_map.get(x),
                                         convert_dtype=False)

    def create_districts(self):
        self.log("Creating districts")
        df = self.df

        district_names = safe_sorted(df['District'].unique())
        districts = [
            District.objects.get_or_create(agency=self.agency, descr=name)[0]
            for name in district_names]
        district_map = {d.descr: d.district_id for d in districts}
        df['District ID'] = df['District'].apply(lambda x: district_map.get(x),
                                                 convert_dtype=False)

    def create_cities(self):
        self.log("Creating cities")
        df = self.df

        city_names = safe_sorted(df['City'].unique())
        cities = [City.objects.get_or_create(descr=name)[0]
                  for name in city_names]
        city_map = {c.descr: c.city_id for c in cities}
        df['City ID'] = df['City'].apply(lambda x: city_map.get(x),
                                         convert_dtype=False)

    def create_priorities(self):
        self.log("Creating priorities")
        df = self.df

        priority_names = safe_sorted(df['Priority'].unique())
        priorities = [Priority.objects.get_or_create(descr=name)[0]
                      for name in priority_names]
        priority_map = {p.descr: p.priority_id for p in priorities}
        df['Priority ID'] = df['Priority'].apply(lambda x: priority_map.get(x),
                                                 convert_dtype=False)

    def create_departments(self):
        self.log("Creating departments")
        df = self.df

        department_names = safe_sorted(df['Department'].unique())
        departments = [Department.objects.get_or_create(descr=name)[0]
                       for name in department_names]
        department_map = {d.descr: d.department_id for d in departments}
        df['Department ID'] = df['Department'].apply(
            lambda x: department_map.get(x),
            convert_dtype=False)

    def create_sources(self):
        self.log("Creating sources")
        df = self.df

        source_tuples = [x for x in pd.DataFrame(
            df.groupby('Source Code')['Source Text'].min()).itertuples()
            if x[0]]
        sources = [CallSource.objects.get_or_create(code=s[0],
                                                    defaults={'descr': s[1]})[0]
                   for s in source_tuples]
        source_map = {s.code: s.call_source_id for s in sources}
        df['Source ID'] = df['Source Code'].apply(lambda x: source_map.get(x),
                                                  convert_dtype=False)

    def create_natures(self):
        self.log("Creating natures")
        df = self.df

        nature_tuples = [x for x in pd.DataFrame(
            df.groupby("Nature Code")['Nature Text'].min()).itertuples()
            if x[0]]
        natures = [
            Nature.objects.get_or_create(key=n[0], defaults={'descr': n[1]})[0]
            for n in nature_tuples]
        nature_map = {n.key: n.nature_id for n in natures}
        df['Nature ID'] = df['Nature Code'].apply(lambda x: nature_map.get(x),
                                                  convert_dtype=False)

    def create_close_codes(self):
        self.log("Creating close codes")
        df = self.df

        close_tuples = [cc for cc in pd.DataFrame(
            df.groupby("Close Code")['Close Text'].min()).itertuples()
            if cc[0]]
        close_codes = [
            CloseCode.objects.get_or_create(code=cc[0],
                                            defaults={'descr': cc[1]})[0]
            for cc in close_tuples]
        close_code_map = {cc.code: cc.close_code_id for cc in close_codes}
        df['Close Code ID'] = df['Close Code'].apply(
            lambda x: close_code_map.get(x),
            convert_dtype=False)

    def create_primary_units(self):
        self.log("Creating primary units")
        df = self.df

        if 'Department ID' in df.columns:
            # Units are per-department; include department in our consideration
            unit_series = df[['Primary Unit', 'Department ID']]

            unit_departments = safe_sorted(
                (c['Primary Unit'], c['Department ID']) for _, c in unit_series.drop_duplicates().iterrows()
                if not isnan(c['Primary Unit'])
            )

            units = []
            for unit, department_id in unit_departments:
                units.append(CallUnit.objects.get_or_create(descr=unit,
                                                            agency=self.agency,
                                                            department_id=department_id)[0])

            unit_map = {u.descr: u.call_unit_id for u in units}
            df['Primary Unit ID'] = df['Primary Unit'].apply(lambda x: unit_map.get(x),
                                                             convert_dtype=False)
        else:
            # No departments; just consider unit names
            unit_series = df['Primary Unit']

            unit_names = safe_sorted(unit_series.unique())
            units = [CallUnit.objects.get_or_create(descr=name, agency=self.agency)[0]
                    for name in unit_names]
            unit_map = {u.descr: u.call_unit_id for u in units}
            df['Primary Unit ID'] = df['Primary Unit'].apply(lambda x: unit_map.get(x),
                                                             convert_dtype=False)


