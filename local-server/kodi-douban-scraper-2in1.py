#!/usr/bin/env python3
import base64
import os
import io
import re
import sqlite3
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from collections import deque
from urllib.parse import quote_plus, unquote_plus
from flask import Flask, request, redirect, url_for, flash, Response, g, make_response, send_file, abort
from pprint import pprint
from gevent.wsgi import WSGIServer


app = Flask(__name__)

###### Configuration Begin ######

app.config['DEBUG'] = True
WEB_PORT = 21958
WEBROOT = 'http://127.0.0.1:{}'.format(WEB_PORT)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache.db')

###### Configuration  End  ######


CLEAR_SUFFIX = [
    'repack', 'unrated',
    '480p', '720p', '1080i', '1080p', '4k',
    'web', 'web-dl', 'bluray', 'blu-ray', 'hdtv',
    'dd5.1', 'dts', 'ddp5.1', 'avc',
    'x264', 'x.264', 'h264', 'h.264',
]
REGEX_SEASON_EPISODE = re.compile('\.s([0-9]+)(e([0-9]+))?')
DIGITS_TO_CHINESE_NUMBER = list(sum(map(lambda s: [s], '零一二三四五六七八九十'), [])) + list(map(lambda s: '十'+s, '一二三四五六七八九'))
CHINESE_NUMBER_TO_DIGITS = dict(zip(DIGITS_TO_CHINESE_NUMBER, map(str, range(len(DIGITS_TO_CHINESE_NUMBER)))))

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        conn = sqlite3.connect(DB_PATH)
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT UNIQUE,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stats (
            key TEXT UNIQUE,
            value INT NOT NULL
        );
        ''')
        conn.row_factory = sqlite3.Row
        db = g._database = conn
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def cache_get(key, func, type='json'):
    assert type in ['json', 'bytes']
    db = get_db()
    cur = db.cursor()
    cur.execute('SELECT value FROM cache WHERE key=?', (key, ))
    row = cur.fetchone()
    cur.execute('UPDATE stats SET value=value+1 WHERE key=?', ('num_query', ))
    if row:
        cur.execute('UPDATE stats SET value=value+1 WHERE key=?', ('num_hit', ))
        db.commit()
        if type == 'json':
            return json.loads(row['value'])
        elif type == 'bytes':
            return base64.decodebytes(bytes(row['value'], 'utf-8'))
        elif type == 'str':
            return row['value']
        else:
            assert False
    else:
        r = func()
        if type == 'json':
            value = r.json()
            value_str = json.dumps(value, indent=2)
        elif type == 'bytes':
            value = r.content
            value_str = str(base64.encodebytes(value), 'ascii')
        elif type == 'str':
            value = r.text
            value_str = r.text
        else:
            assert False
        cur.execute('INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)', (key, value_str))
        db.commit()
        return value


def xmlify(root):
    sio = io.StringIO()
    ET.ElementTree(root).write(sio, xml_declaration=True, encoding='unicode')
    xml = sio.getvalue()
    if app.config['DEBUG']:
        import xml.dom.minidom as minidom
        reparsed = minidom.parseString(xml)
        xml = reparsed.toprettyxml(indent='    ', encoding='utf-8')
    return Response(xml, mimetype='text/xml')


def get_title_from_filename(filename):
    """
    >>> get_title_from_filename('')
    ('', None, None, None)
    >>> get_title_from_filename('Kingsman.The.Secret.Service.2014.UNRATED.720p.BluRay.DD5.1.x264-PuTao')
    ('kingsman the secret service', 2014, None, None)
    >>> get_title_from_filename('Kingsman.The.Secret.Service.2014.UNRATED.1080p.BluRay.DTS.x264-PuTao')
    ('kingsman the secret service', 2014, None, None)
    >>> get_title_from_filename('Atomic.Blonde.2017.1080p.WEB-DL.DD5.1.H264-FGT.mkv')
    ('atomic blonde', 2017, None, None)
    >>> get_title_from_filename('Atomic.Blonde.2017.720p.BluRay.x264.DTS-HDChina')
    ('atomic blonde', 2017, None, None)
    >>> get_title_from_filename('Annihilation.2018.1080p.BluRay.x264.Atmos.TrueHD7.1-HDChina')
    ('annihilation', 2018, None, None)
    >>> get_title_from_filename('')
    ('', None, None, None)
    >>> get_title_from_filename('House.Of.Cards.2013.S01.720p.BluRay.x264-DEMAND')
    ('house of cards', 2013, 1, None)
    >>> get_title_from_filename('House.of.Cards.2013.S02.720p.BluRay.x264-DEMAND')
    ('house of cards', 2013, 2, None)
    >>> get_title_from_filename('Person.of.Interest.S02.720p.BluRay.DD5.1.x264-DON')
    ('person of interest', None, 2, None)
    >>> get_title_from_filename('Person.of.Interest.S04.720p.BluRay.x264-DEMAND')
    ('person of interest', None, 4, None)
    >>> get_title_from_filename('Billions.S01.720p.HDTV.x264-Scene')
    ('billions', None, 1, None)
    >>> get_title_from_filename('Person.of.Interest.S01.720p.Bluray.DD5.1.x264-DON')
    ('person of interest', None, 1, None)
    >>> get_title_from_filename('Person.of.Interest.S03.720p.BluRay.DD5.1.x264-NTb')
    ('person of interest', None, 3, None)
    >>> get_title_from_filename('Person.of.Interest.S05.BluRay.720p.x264.DTS-HDChina')
    ('person of interest', None, 5, None)
    >>> get_title_from_filename('Silicon.Valley.S03.720p.BluRay.DD5.1.x264-ZQ')
    ('silicon valley', None, 3, None)
    >>> get_title_from_filename('How.to.Get.Away.with.Murder.S04E01.REPACK.720p.HDTV.x264-KILLERS.mkv')
    ('how to get away with murder', None, 4, 1)
    >>> get_title_from_filename('How.to.Get.Away.with.Murder.S04E02.720p.HDTV.x264-KILLERS.mkv')
    ('how to get away with murder', None, 4, 2)
    >>> get_title_from_filename('How.to.Get.Away.With.Murder.S01.1080p.WEB-DL.DD5.1.H.264-BS')
    ('how to get away with murder', None, 1, None)
    >>> get_title_from_filename('Billions.S02.720p.AMZN.WEBRip.DD5.1.x264-NTb')
    ('billions', None, 2, None)
    >>> get_title_from_filename('Silicon.Valley.S04.1080p.BluRay.x264-ROVERS')
    ('silicon valley', None, 4, None)
    >>> get_title_from_filename('Silicon.Valley.S05.720p.AMZN.WEB-DL.DDP5.1.H.264-NTb')
    ('silicon valley', None, 5, None)
    >>> get_title_from_filename('13.Reasons.Why.S02.1080p.WEB.x264-STRiFE')
    ('13 reasons why', None, 2, None)
    >>> get_title_from_filename('Rick and Morty S03 1080p Blu-ray AVC TrueHD 5.1-CtrlHD')
    ('rick and morty', None, 3, None)
    >>> get_title_from_filename('Sense8.S00E02.Amor.Vincit.Omnia.1080p.NF.WEB-DL.DD5.1.x264-NTb.mkv')
    ('sense8', None, 0, 2)
    >>> get_title_from_filename('Billions.S03.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb')
    ('billions', None, 3, None)
    """
    name = filename.lower().replace(' ', '.')
    season, episode = None, None
    end = len(name)

    match = REGEX_SEASON_EPISODE.search(name)
    if match:
        season, _, episode = match.groups()
        season = int(season)
        if episode:
            episode = int(episode)
        end = min(end, match.start())

    for suffix in CLEAR_SUFFIX:
        idx = name.find(suffix)
        if idx != -1:
            end = min(end, idx)

    split = name[:end].replace('.', ' ').strip().split()
    year = None
    if len(split) > 1:
        try:
            year = int(split[-1])
        except ValueError:
            pass
    if year is not None and 1900 <= year <= 2100:
        split = split[:-1]
    title = ' '.join(split)
    return title, year, season, episode


def replace_chinese_season_number(title):
    for digit, chinese in enumerate(DIGITS_TO_CHINESE_NUMBER):
        title = title.replace('第{}季'.format(chinese), '第{:02d}季'.format(digit))
    return title


@app.route('/GetSearchResults/<filename>')
def GetSearchResults(filename):
    title, year, season, episode = get_title_from_filename(filename)
    print('(title, year, season, episode) =', repr((title, year, season, episode)))
    value = cache_get('search:' + title, lambda: requests.get('https://api.douban.com/v2/movie/search', params=dict(q=title)))
    # pprint(value)

    subjects = deque()
    for subject in value['subjects']:
        try:
            subject_year = int(subject['year'])
        except:
            subject_year = None
        if not (subject_year is None or year is None or subject_year-1 <= year <= subject_year+1):
            continue
        prepend = False
        if season is not None:
            str_chinese_season = '第{}季'.format(DIGITS_TO_CHINESE_NUMBER[season])
            prepend = str_chinese_season in subject['title']
        if prepend:
            subjects.appendleft(subject)
        else:
            subjects.append(subject)

    root = ET.Element('results')
    root.attrib['sorted'] = 'yes'
    for subject in subjects:
        entity = ET.SubElement(root, 'entity')
        ET.SubElement(entity, 'title').text = replace_chinese_season_number(subject['title'])
        url = '{}/GetDetails/{}'.format(WEBROOT, subject['id'])
        if episode is not None:
            url += '?episode={}'.format(episode)
        ET.SubElement(entity, 'url').text = url
    return xmlify(root)


@app.route('/GetDetails/<int:subject_id>')
def GetDetails(subject_id):
    value = cache_get('subject:{}'.format(subject_id), lambda: requests.get('https://api.douban.com/v2/movie/subject/{}'.format(subject_id)))

    try:
        episode = int(request.args['episode'])
    except:
        episode = None
    title = replace_chinese_season_number(value['title'])
    if episode is not None:
        title += ' 第{:02d}集'.format(episode)

    root = ET.Element('details')
    ET.SubElement(root, 'title').text = title
    ET.SubElement(root, 'rating').text = '{:.1f}'.format(value['rating']['average'])
    if 'ratings_count' in value:
        ET.SubElement(root, 'votes').text = '{}'.format(value['ratings_count'])
    if 'year' in value:
        ET.SubElement(root, 'year').text = value['year']
    if 'summary' in value:
        ET.SubElement(root, 'plot').text = value['summary']
    if 'originaltitle' in value:
        ET.SubElement(root, 'original_title').text = value['originaltitle']
    if 'directors' in value:
        for director in value['directors']:
            ET.SubElement(root, 'director').text = director.get('name', '')
    if episode is None and 'images' in value and 'large' in value['images']:
        ET.SubElement(root, 'thumb').text = '{}/GetImage?url={}'.format(WEBROOT, quote_plus(value['images']['large']))
    if 'genres' in value:
        for genre in value['genres']:
            ET.SubElement(root, 'genre').text = genre
    if 'casts' in value:
        for cast in value['casts']:
            actor = ET.SubElement(root, 'actor')
            ET.SubElement(actor, 'name').text = cast['name']
            if 'avatars' in cast and 'large' in cast['avatars']:
                ET.SubElement(actor, 'thumb').text = '{}/GetImage?url={}'.format(WEBROOT, quote_plus(cast['avatars']['large']))
    if 'countries' in value:
        for country in value['countries']:
            ET.SubElement(root, 'country').text = country

    return xmlify(root)


@app.route('/GetImage')
def GetImage():
    url = request.args['url']
    print('GetImage', url)
    content = cache_get('image:'+url, lambda: requests.get(url), type='bytes')
    return send_file(io.BytesIO(content), mimetype='image/jpeg', as_attachment=False)



if __name__ == '__main__':
    if app.config['DEBUG']:
        app.run(port=WEB_PORT)
    else:
        http_server = WSGIServer(('127.0.0.1', WEB_PORT), app)
        try:
            print('WSGIServer start')
            http_server.serve_forever()
        except KeyboardInterrupt:
            print('WSGIServer stopped')
