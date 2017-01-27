from __future__ import print_function
from datetime import datetime
from HTMLParser import HTMLParser
import boto3
from boto3.session import Session
import requests

TTS_ENDPOINT = 'http://tsn.baidu.com/text2audio'
TTS_TOKEN = '25.f1aac8feab2f71f330bb597556de0997.315360000.1799467929.282335-9190441'
TTS_HEADER = {'Content-Type': 'audio/mp3'}
TTS_CHUNK_SIZE = 333

S3_ENDPOINT = 'https://zhihu.s3.amazonaws.com/'
PRE_NEWS = 'support/pre_news_'

NEWS_ENDPOINT = 'http://news-at.zhihu.com/api/4/news/latest'
STORY_ENDPOINT = 'http://news-at.zhihu.com/api/4/news/'
ZHIHU_HEADER={
    'Content-Type': 'text/html',
    'Accept-Language': 'en-US,en;q=0.8,zh-CN;q=0.6,zh;q=0.4',
    'Accept': 'en-US,en;q=0.8,zh-CN;q=0.6,zh;q=0.4',
    'Accept-Encoding': 'gzip, deflate, sdch, br',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/55.0.2883.95 Safari/537.36'
}

ST_READ_NEWS = 'ST_READ_NEWS'
ST_LIST_NEWS = 'ST_LIST_NEWS'
TOKEN_DELIMITER = '|'

INDEX_DIC = {
    '1st': 1,
    '2nd': 2,
    'second': 2,
    '3rd': 3,
    '4th': 4,
    '5th': 5,
    '6th': 6,
    '7th': 7,
    '8th': 8,
    '9th': 9,
    '10th': 10
}

class MLStripper(HTMLParser):
    def __init__(self):
        self.reset()
        self.fed = []
    def handle_data(self, d):
        self.fed.append(d)
    def get_data(self):
        return ''.join(self.fed)

# ----------------Global variables----------------------------------------------
news_title_list = []
read_news_list = []
paused_token = None
current_news_index = 0

# --------------- Helpers that build all of the responses ----------------------
def build_speechlet_response(output, title, reprompt_text):
    return {
        'outputSpeech': {
            'type': 'PlainText',
            'text': output
        },
        'card': {
            'type': 'Simple',
            'title': "SessionSpeechlet - " + title,
            'content': "SessionSpeechlet - " + output
        },
        'reprompt': {
            'outputSpeech': {
                'type': 'PlainText',
                'text': reprompt_text
            }
        },
        'shouldEndSession': True
    }

def build_audio_stop_response():
    return {
        "directives": [
            {
                "type": "AudioPlayer.Stop"
            }
        ],
        'shouldEndSession': True
    }

def build_audio_play_response(play_type, play_behavior, token, url, offset):
    return {
        "directives": [
            {
                "type": play_type,
                "playBehavior": play_behavior,
                "audioItem": {
                    "stream": {
                        "token": token,
                        "url": url,
                        "offsetInMilliseconds": offset
                    }
                }
            }
        ],
        'shouldEndSession': True
    }

def build_response(response):
    print('---RESPONSE SENT---')
    return {
        'version': '1.0',
        'sessionAttributes': {},
        'response': response
    }

# --------------- Help Functions that handle text and s3 -----------------------
def get_tts_content(tex):
    options = {
        'lan': 'zh',
        'ctp': '1',
        'cuid': 'zhihuDaily_alexa',
        'tex': tex,
        'tok': TTS_TOKEN
    }
    response = requests.get(TTS_ENDPOINT, params=options, headers=TTS_HEADER)
    if response.status_code == 200:
        return response.content
    return None

def get_boto3_session():
    return  Session(
        aws_access_key_id='AKIAJJ5Y3AQT44XX5OAQ',
        aws_secret_access_key='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',
        region_name='us-east-1'
    )
    
def put_to_s3(key, content):
    session = get_boto3_session()
    s3 = session.resource('s3')
    s3.Object('zhihu', key).put(Body=content, ACL='public-read')
    return S3_ENDPOINT + key
    
def compare_title_s3(item1, item2):
    [date1, story1, title1] = item1.split('/')
    [date2, story2, title2] = item2.split('/')
    date_compare = int(date1) - int(date2)
    if date_compare != 0:
        return date_compare
    story_compare = int(story1) - int(story2)
    if story_compare != 0:
        return story_compare
    [index1, _] = title1.split('.')
    [index2, _] = title2.split('.')
    if index1 == 'title':
        return -1
    if index2 == 'title':
        return 1
    return int(index1) - int(index2)
    
def get_dir_in_s3(dir_key):
    session = get_boto3_session()
    s3 = session.resource('s3')
    bucket = s3.Bucket('zhihu')
    file_list = bucket.objects.filter(Marker=dir_key, Prefix=dir_key)
    key_list = [x.key for x in file_list if x.key != str(dir_key + '/')]
    if len(key_list) == 0:
        return None
    key_list.sort(compare_title_s3)
    [_, current_story_id, _] = key_list[0].split('/')
    current_story = []
    story_list = []
    for i in range(0, len(key_list)):
        [_, story_id, key] = key_list[i].split('/')
        if story_id != current_story_id:
            story_list.append(current_story)
            current_story = []
            current_story_id = story_id
        if key != '':
            current_story.append(S3_ENDPOINT + str(key_list[i]))
    story_list.append(current_story)
    print('in story list')
    print(str(story_list))
    return story_list

def put_story_to_s3(date, story):
    audios = get_news_audio(story)
    parent_dir = date + '/' + story['id']
    audio_urls = [put_to_s3(parent_dir + '/title.mp3', audios[0])]
    audio_urls.extend([put_to_s3(parent_dir + '/' + str(i) + '.mp3', x) for (i, x) in enumerate(audios[1:])])
    return audio_urls

def put_story_list_to_s3(date, story_list):
    news = get_formated_news(date, story_list)
    return [put_story_to_s3(date, x) for x in news['stories']]

def get_news_audio(story):
    content = [story['title']]
    content.extend(story['chunked_content'])
    # assuming no None is returned
    return [get_tts_content(x) for x in content]

def get_story_id_from_s3(news_list):
    story_id_list = []
    for i in range(0, len(news_list)):
        story_id = news_list[i][0].split('/')[4]
        story_id_list.append(story_id)
    return story_id_list

def get_storys_from_dynamodb(date):
    session = get_boto3_session()
    dynamodb = session.resource('dynamodb')
    table = dynamodb.Table('zhihu')
    response = table.get_item(Key={'date': date})
    return response['Item'] if 'Item' in response else []

def put_storys_to_dynamodb(date, story_ids, story_list):
    session = get_boto3_session()
    dynamodb = session.resource('dynamodb')
    table = dynamodb.Table('zhihu')
    table.put_item(Item={'date': date, 'story_ids': story_ids, 'story_list': story_list})

def sync_story_to_s3_dynamodb(date, new_story_id_list, cached_story_id_list, cached_story_list):
    news = get_formated_news(date, new_story_id_list)
    new_story = news['stories']
    print('enter')
    for i in range(0, len(new_story)):
        story = new_story[i]
        story_id = new_story_id_list[i]
        story_list = put_story_to_s3(date, story)
        cached_story_id_list.append(story_id)
        cached_story_list.append(story_list)
        put_storys_to_dynamodb(date, cached_story_id_list, cached_story_list)
        print('synced ' + str(story_id))
    print('end')

def load_latest_news_1():
    latest_date = datetime.now().strftime('%Y%m%d')
    return get_storys_from_dynamodb(latest_date)

def store_latest_news(a, b):
    raw_news = get_latest_news()
    latest_story_id_list = []
    date = None
    if raw_news is not None:
        date = raw_news['date']
        latest_story_id_list = [str(x['id']) for x in raw_news['stories']]
    if date is None:
        return
    cached_storys = get_storys_from_dynamodb(date)
    cached_story_ids = []
    cached_news_list = []
    if len(cached_storys) > 0:
        cached_story_ids = cached_storys['story_ids']
        cached_news_list = cached_storys['story_list']
    new_story_id_list = list(set(latest_story_id_list) - set(cached_story_ids))
    print('new_story_id_list')
    print(str(new_story_id_list))
    #a=get_dir_in_s3(date)
    #print(str(get_story_id_from_s3(a)))
    sync_story_to_s3_dynamodb(date, new_story_id_list, cached_story_ids, cached_news_list)

def load_latest_news():
    latest_date = datetime.now().strftime('%Y%m%d')
    news_list = get_dir_in_s3(latest_date)
    raw_news = get_latest_news()
    latest_story_id_list = []
    date = None
    if raw_news is not None:
        date = raw_news['date']
        latest_story_id_list = [str(x['id']) for x in raw_news['stories']]
    if date != latest_date:
        return []
    if news_list is not None:
        story_id_list = get_story_id_from_s3(news_list)
        new_story_id_list = list(set(latest_story_id_list) - set(story_id_list))
    else:
        news_list = []
        new_story_id_list = latest_story_id_list
    print('new_story_to_store')
    print(str(new_story_id_list))
    new_story_list = put_story_list_to_s3(date, new_story_id_list)
    # read the newly added story since last call
    news_list.extend(new_story_list)
    print('list news')
    print(str(news_list))
    return news_list

def get_latest_news():
    response = requests.get(NEWS_ENDPOINT, headers=ZHIHU_HEADER)
    if response.status_code == 200:
        return response.json()
    return None

def get_formated_news(date, story_ids):
    return {
        'date': date,
        'stories': [get_formated_story(id) for id in story_ids]
    }

def get_story(story_id):
    response = requests.get(STORY_ENDPOINT + story_id, headers=ZHIHU_HEADER)
    if response.status_code == 200:
        return response.json()
    return None

def get_formated_story(story_id):
    raw_story = get_story(story_id)
    if raw_story:
        body = raw_story['body']
        s = MLStripper()
        s.feed(body)
        raw_content= s.get_data()
        content_no_lines = raw_content.replace('\r\n', ' ')
        content_no_space = content_no_lines.replace('\n', '')
        return {
            'id': str(story_id),
            'title': raw_story['title'],
            'chunked_content': get_chunked_story(content_no_space),
        }
    return None

def get_chunked_story(content):
    if content:
        return [content[i : i + TTS_CHUNK_SIZE] for i in range(0, len(content), TTS_CHUNK_SIZE)]
    return None

# --------------- Functions that control the skill's behavior ------------------
def list_news(intent):
    news = load_latest_news()
    if len(news) == 0:
        return build_response(build_speechlet_response('We encountered an error when trying to load latest news, please try later', 'Error', None))
    titles = [x[0] for x in news]
    list_news_content = []
    for i in range(0, len(news)):
        list_news_content.append(S3_ENDPOINT + PRE_NEWS + str(i + 1) + '.mp3')
        list_news_content.append(titles[i])
    list_news_content.append(S3_ENDPOINT + 'support/post_list.mp3')
    global news_title_list
    news_title_list = list_news_content
    print(str(list_news_content))
    return build_response(build_audio_play_response(
            'AudioPlayer.Play', 'REPLACE_ALL', ST_LIST_NEWS + '|1', news_title_list[0], 0))

def read_nth_news(intent):
    if 'Index' in intent['slots']:
        index = intent['slots']['Index']['value']
    if index:
        print('read ' + str(index))
        return read_news(INDEX_DIC[str(index)] - 1, intent)

def read_news(index, intent):
    news = load_latest_news()
    if len(news) == 0:
        return build_response(build_speechlet_response('We encountered an error when trying to read the news, please try later', 'Error', None))
    if index >= len(news):
        return build_response(build_speechlet_response('There are total ' + str(len(news)) + 'news. Can not read ' + str(index + 1) + 'th news.', 'Error', None))
    global current_news_index
    current_news_index = index
    news_to_read = news[index]
    news_to_read_content = [
        S3_ENDPOINT + 'support/pre_title.mp3',
        news_to_read[0],
        S3_ENDPOINT + 'support/pre_content.mp3'
    ]
    news_to_read_content.extend(news_to_read[1:])
    news_to_read_content.append(S3_ENDPOINT + 'support/post_news.mp3')
    global read_news_list
    read_news_list = news_to_read_content
    return build_response(build_audio_play_response(
        'AudioPlayer.Play', 'REPLACE_ALL', ST_READ_NEWS + '|1', read_news_list[0], 0))

def set_paused_audio(audio_request):
    global paused_token
    paused_token = audio_request['token']
    global paused_offset
    paused_offset = audio_request['offsetInMilliseconds']
    print('---pause token: ' + paused_token + ' pause offset: ' + str(paused_offset) + '---')

def handle_audio_nearly_finish(token):
    [state, index] = token.split(TOKEN_DELIMITER)
    index = int(index)
    next_token = state + '|' + str(index + 1)
    next_audio = None
    if state == ST_LIST_NEWS and len(news_title_list) > 0:
        if index ==len(news_title_list):
            return build_audio_stop_response()
        next_audio = news_title_list[index]
    elif state == ST_READ_NEWS and len(read_news_list) > 0:
        if index ==len(read_news_list):
            return build_audio_stop_response()
        next_audio = read_news_list[index]
    if next_audio:
        print('---Enque Song: ' + next_audio)
        return build_response(build_audio_play_response(
            'AudioPlayer.Play', 'REPLACE_ENQUEUED', next_token, next_audio, 0))

def pause():
    return build_response(build_audio_stop_response())

def resume(resume_request):
    if paused_token:
        print('---pause token: ' + paused_token + ' pause offset: ' + str(paused_offset) + '---')
        [state, index] = paused_token.split(TOKEN_DELIMITER)
        index = int(index)
        audio = None
        if state == ST_LIST_NEWS:
            audio = news_title_list[index - 1]
        elif state == ST_READ_NEWS:
            audio = read_news_list[index - 1]
        return build_response(build_audio_play_response(
            'AudioPlayer.Play', 'REPLACE_ALL', paused_token, audio, paused_offset))
    else:
        return read_news(0, resume_request)

def skip(skip_request):
    global current_news_index
    if current_news_index < len(read_news_list):
        current_news_index = current_news_index + 1
        return read_news(current_news_index, skip_request)
    else:
        return build_response(build_speechlet_response('End of the list, you can use list command to reload the latest news', 'Error', None))

def handle_session_end_request():
    card_title = "Session Ended"
    speech_output = "Thank you for trying Zhihu daily" \
                    "Have a nice day! "
    # Setting this to true ends the session and exits the skill.
    should_end_session = True
    return build_response({}, build_speechlet_response(
        card_title, speech_output, None))

# --------------- Events ------------------
def on_audio_request(audio_request):
    request_type = audio_request['type']
    print("---Audio request---" + request_type)
    if request_type == 'AudioPlayer.PlaybackNearlyFinished':
        return handle_audio_nearly_finish(audio_request['token'])
    elif request_type == 'AudioPlayer.PlaybackStopped':
        return set_paused_audio(audio_request)

def on_launch(intent):
    """ Called when the user launches the skill without specifying what they
    want
    """
    print("on_launch requestId=" + intent['requestId'])
    # Dispatch to your skill's launch
    return list_news(intent)

def on_intent(intent_request):
    """ Called when the user specifies an intent for this skill """
    print("on_intent requestId=" + intent_request['requestId'])
    intent = intent_request['intent']
    intent_name = intent_request['intent']['name']
    print("Intent name=" + intent_name)
    # Dispatch to your skill's intent handlers
    if intent_name == "ListNewsIntent":
        return list_news(intent)
    elif intent_name == "ReadNthNewsIntent":
        return read_nth_news(intent)
    elif intent_name == "ReadNewsIntent":
        return read_news(0, intent)
    elif intent_name == "AMAZON.PauseIntent":
        return pause()
    elif intent_name == "AMAZON.ResumeIntent":
        return resume(intent)
    elif intent_name == "AMAZON.NextIntent":
        return skip(intent)
    elif intent_name == "AMAZON.CancelIntent" or intent_name == "AMAZON.StopIntent":
        return handle_session_end_request()
    else:
        raise ValueError("Invalid intent")

def on_session_ended(session_ended_request):
    """ Called when the user ends the session.
    Is not called when the skill returns should_end_session=true
    """
    print("on_session_ended requestId=" + session_ended_request['requestId'])
    print("This shall not be called")

# --------------- Main handler ------------------
def lambda_handler(event, context):
    print('---REQUEST GET---')
    request = event['request']
    event_type = request['type']
    print('---Request type: ' + event_type)
    if event_type.startswith('AudioPlayer'):
        return on_audio_request(request)
    elif event['request']['type'] == "LaunchRequest":
        return on_launch(request)
    elif event['request']['type'] == "IntentRequest":
        return on_intent(request)
    elif event['request']['type'] == "SessionEndedRequest":
        return on_session_ended(request)
