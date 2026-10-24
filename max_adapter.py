"""MAX integration boundary.

This module intentionally does not claim working MAX bot integration until
MAX developer credentials, event signature verification and API contract are
configured and tested. Core services are shared through features.py.
"""
from features import news_list, user

def handle_normalized_event(platform: str, user_id: str, command: str):
    if platform not in {'telegram','max'}:
        raise ValueError('Unknown platform')
    if command == 'news':
        return {'type':'news','items':news_list()[:5]}
    if command == 'profile':
        return {'type':'profile','profile':user(platform,user_id)}
    return {'type':'help','text':'Расписание и новости РКТМ'}
