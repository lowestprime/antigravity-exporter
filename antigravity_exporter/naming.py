"""Codex-compatible filename vocabulary, adapted under MIT; see notices.

An ordinary Antigravity export changes the product prefix, not the timestamp
syntax. Legacy rc1 fields remain valid in saved custom templates.
"""
from datetime import datetime
import os
import re
from zoneinfo import ZoneInfo

DEFAULT_TEMPLATE='antigravity_{title}_{mode}_{stamp}{session_short_part}{counts}.md'
STABLE_TEMPLATE='antigravity_{title}_{mode}{session_short_part}{counts}.md'
FIELDS={'title','mode','stamp','session_id','session_short','session_short_part','lines','tokens',
        'token_method','token_encoding','counts','id','short_id','project','timestamp','first_step',
        'last_step','privacy','encoding'}


def safe_slug(text,max_len=80):
    text=re.sub(r'[^\w\s.-]+','',str(text).strip().lower(),flags=re.UNICODE)
    text=re.sub(r'\s+','_',text)
    text=re.sub(r'_+','_',text).strip('._- ')
    return (text or 'antigravity_thread')[:max_len].strip('._- ')


def abbrev_tz(name):
    s=(name or '').strip()
    if ' ' in s:
        words=s.split()
        return ''.join(w[0] for w in words).upper() if len(words)>=2 and all(w[0].isalpha() for w in words) else ''.join(words)
    return s


def export_stamp(now=None):
    if now is None:
        tz=os.environ.get('AGY_EXPORT_TZ','').strip()
        try: now=datetime.now(ZoneInfo(tz)) if tz else datetime.now().astimezone()
        except (ValueError,KeyError): now=datetime.now().astimezone()
    head=now.strftime('%a_%m%d%Y_%I%M%S_%p').upper()
    zone=abbrev_tz(now.strftime('%Z'))
    return f'{head}-{zone}' if zone else head


def mode_descriptor(args):
    mode=str(args.mode)
    if mode in ('response','turn','response-block') and args.turn: mode+=f'-{args.turn:04d}'
    elif mode=='message' and args.message: mode+=f'-{args.message:04d}'
    elif mode=='range':
        low=args.from_step if args.from_step is not None else args.from_message or 1
        high=args.to_step if args.to_step is not None else args.to_message or 0
        mode+=f'-{low:04d}-{high:04d}'
    if args.last_n_turns: mode+=f'-last-{args.last_n_turns}-turns'
    if args.last_n_steps: mode+=f'-last-{args.last_n_steps}-steps'
    return safe_slug(mode)


def count_suffix(counts):
    n=counts.get('tokens')
    if n is None: return f"_{counts['lines']}lines"
    if counts.get('exact_for_selected_encoding'):
        return f"_{counts['lines']}lines_{n}{safe_slug(counts.get('encoding') or 'encoding',30)}_tokens"
    return f"_{counts['lines']}lines_approx{n}tokens"


def filename_stem(template,values):
    name=template.format_map(values)
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).strip(' .')
    if name.lower().endswith('.md'): name=name[:-3]
    if not name: raise ValueError('Filename template produced an empty filename')
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',name,re.I):name='_'+name
    # Reserve room for the longest standard member suffix (.manifest.json).
    return name[:220].rstrip(' .')
