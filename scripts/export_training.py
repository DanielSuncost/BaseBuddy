#!/usr/bin/env python3
"""Export gallery detections for offline training (CLI)."""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    parser = argparse.ArgumentParser(description='Export BaseBuddy detections for training')
    parser.add_argument('--hours', type=int, default=168, help='Lookback window (default 168)')
    parser.add_argument('--format', choices=('json', 'yolo'), default='json')
    parser.add_argument('--output', '-o', default='-', help='Output file (- for stdout, or path)')
    args = parser.parse_args()

    from basebuddy.modules.database import AnalyticsDB
    db = AnalyticsDB()
    rows = db.get_events_for_export(hours=args.hours)
    zones = db.list_false_positive_zones(limit=5000)

    if args.format == 'json':
        payload = {
            'detections': rows,
            'false_positive_zones': zones,
            'count': len(rows),
        }
        text = json.dumps(payload, indent=2)
        if args.output == '-':
            print(text)
        else:
            with open(args.output, 'w', encoding='utf-8') as fh:
                fh.write(text)
            print(f'Wrote {len(rows)} detections to {args.output}')
        return

    print('For YOLO zip export use the gallery UI or GET /api/gallery/export?format=yolo', file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    main()
