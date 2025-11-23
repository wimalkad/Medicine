import os
from flask import Flask, render_template, request, jsonify, session
import google.generativeai as genai
from datetime import datetime, timedelta
import secrets
import json
import markdown
import re

app = Flask(__name__)
app.secret_key = os.getenv('SESSION_SECRET', secrets.token_hex(32))

genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

# База знаний по здоровью
HEALTH_KNOWLEDGE_BASE = {
    'питание': {
        'вода': 'Пейте 8 стаканов воды в день (около 2 литров). Вода помогает пищеварению и выводит токсины.',
        'белок': 'Взрослому человеку нужно 0.8-1г белка на кг веса. Источники: мясо, рыба, яйца, бобовые.',
        'витамины': 'Получайте витамины из разнообразной пищи: фрукты, овощи, орехи, зелень.',
        'завтрак': 'Завтракайте в течение часа после пробуждения. Это запускает метаболизм.',
        'сахар': 'Ограничьте сахар до 25г в день. Избыток сахара ведет к диабету и ожирению.',
    },
    'фитнес': {
        'кардио': '150 минут умеренной активности в неделю. Бег, плавание, велосипед.',
        'силовые': 'Тренируйте мышцы 2-3 раза в неделю. Используйте вес тела или гантели.',
        'растяжка': 'Делайте растяжку после тренировок. Это предотвращает травмы.',
        'разминка': 'Всегда начинайте с 5-10 минут разминки перед тренировкой.',
        'отдых': 'Давайте мышцам отдыхать 48 часов между силовыми тренировками.',
    },
    'сон': {
        'продолжительность': 'Взрослым нужно 7-9 часов сна. Подросткам - 8-10 часов.',
        'режим': 'Ложитесь и вставайте в одно время. Это улучшает качество сна.',
        'экраны': 'Не смотрите в экраны за час до сна. Синий свет мешает засыпанию.',
        'температура': 'Оптимальная температура для сна 18-20°C.',
    },
    'психология': {
        'стресс': 'Практикуйте дыхательные упражнения, медитацию, йогу для снижения стресса.',
        'социализация': 'Общение с близкими улучшает психическое здоровье.',
        'хобби': 'Занимайтесь любимым делом минимум 30 минут в день.',
    },
    'гигиена': {
        'руки': 'Мойте руки 20 секунд с мылом после улицы и перед едой.',
        'зубы': 'Чистите зубы 2 раза в день по 2 минуты. Используйте зубную нить.',
    }
}

SYSTEM_PROMPT = """Вы - полезный ассистент по здоровью. Вы даете советы по образу жизни, питанию и фитнесу на русском языке. 

Отвечайте на русском языке. Будьте дружелюбны, полезны и поддерживайте здоровый образ жизни.

Используйте базу знаний для дополнения ответов, когда это уместно."""

@app.route('/')
def index():
    if 'chat_history' not in session:
        session['chat_history'] = []
    if 'profile' not in session:
        session['profile'] = {
            'age': None,
            'gender': None,
            'health_stats': None,
            'goals': [],
            'allergies': [],
            'activity_level': None
        }
    if 'medications' not in session:
        session['medications'] = []
    if 'reminders' not in session:
        session['reminders'] = []
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    if not request.json:
        return jsonify({'error': 'Некорректный запрос'}), 400
    
    user_message = request.json.get('message', '')
    
    if not user_message:
        return jsonify({'error': 'Пустое сообщение'}), 400
    
    if 'chat_history' not in session:
        session['chat_history'] = []
    
    # Всегда сохраняем сообщение пользователя
    session['chat_history'].append({
        'role': 'user',
        'content': user_message,
        'timestamp': datetime.now().strftime('%H:%M')
    })
    
    # Проверяем, является ли это командой
    is_command = user_message.startswith('/profile') or user_message.startswith('/medication') or user_message.startswith('/reminder') or user_message.startswith('/knowledge')
    
    # Если это команда, обрабатываем её напрямую БЕЗ LLM
    if user_message.startswith('/profile'):
        return handle_profile_command(user_message)
    
    if user_message.startswith('/medication'):
        return handle_medication_command(user_message)
    
    if user_message.startswith('/reminder'):
        return handle_reminder_command(user_message)
    
    if user_message.startswith('/knowledge'):
        return handle_knowledge_command(user_message)
    
    # Проверяем, не пытается ли пользователь использовать функционал команд обычным языком
    # Только если это НЕ команда
    if not is_command:
        command_suggestion = suggest_command(user_message)
        if command_suggestion:
            session['chat_history'].append({
                'role': 'assistant',
                'content': command_suggestion,
                'timestamp': datetime.now().strftime('%H:%M')
            })
            session.modified = True
            return jsonify({
                'response': command_suggestion,
                'timestamp': datetime.now().strftime('%H:%M')
            })
    
    # Если это не команда и нет предложения команды, используем LLM
    try:
        # Поиск в базе знаний
        knowledge_context = search_knowledge_base(user_message)
        
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
        
        if knowledge_context:
            messages.append({'role': 'system', 'content': f"Релевантная информация из базы знаний: {knowledge_context}"})
        
        if session.get('profile'):
            profile_info = "Информация о пользователе: "
            profile = session['profile']
            
            if profile.get('age'):
                profile_info += f"Возраст: {profile['age']}, "
            if profile.get('gender'):
                profile_info += f"Пол: {profile['gender']}, "
            if profile.get('health_stats'):
                profile_info += f"Здоровье: {profile['health_stats']}, "
            if profile.get('activity_level'):
                profile_info += f"Уровень активности: {profile['activity_level']}, "
            if profile.get('goals'):
                profile_info += f"Цели: {', '.join(profile['goals'])}, "
            if profile.get('allergies'):
                profile_info += f"Аллергии: {', '.join(profile['allergies'])}"
            
            messages.append({'role': 'system', 'content': profile_info})
        
        for msg in session['chat_history'][-10:]:
            messages.append({
                'role': msg['role'],
                'content': msg['content']
            })
        
        # Форматируем историю для Gemini
        prompt_parts = []
        for msg in messages:
            role_prefix = "Система" if msg['role'] == 'system' else ("Пользователь" if msg['role'] == 'user' else "Ассистент")
            prompt_parts.append(f"{role_prefix}: {msg['content']}")
        
        full_prompt = "\n\n".join(prompt_parts)
        
        response = model.generate_content(
            full_prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.7,
                max_output_tokens=2048,
            )
        )
        
        raw_message = response.text if response and response.text else "Извините, не удалось получить ответ. Попробуйте еще раз."
        
        # Конвертируем Markdown в HTML
        assistant_message = markdown.markdown(raw_message, extensions=['nl2br', 'fenced_code'])
        
        medical_keywords = [
            'симптом', 'болезн', 'боль', 'боли', 'болит', 'болят', 'болел', 'болела', 'болело', 'болели',
            'болею', 'болеешь', 'болеет', 'болеем', 'болеете', 'болеют', 'болеть', 'переболел',
            'болен', 'больна', 'больны', 'больной', 'больным', 'больного', 'больных',
            'заболевание', 'заболел', 'заболела', 'заболели', 'заболеть', 'заболеваю', 'заболевают', 'температур', 'жар', 'лихорадка',
            'давлени', 'диагноз', 'лечен', 'лечить', 'лечу', 'лечусь', 'вылечить', 'излечить',
            'препарат', 'таблетк', 'лекарств', 'медикамент', 'врач', 'доктор', 'специалист',
            'госпитал', 'клиник', 'больниц', 'поликлиник', 'анализ', 'обследован',
            'терапи', 'инфекц', 'вирус', 'бактери', 'ковид', 'covid', 'коронавирус', 'коронавируса', 'коронавирусом',
            'простуд', 'простыл', 'простыла', 'простыли', 
            'простужен', 'простужена', 'простужены', 'орви', 'орз',
            'грипп', 'гриппом', 'гриппа', 'ангина', 'ангиной', 'ангину', 'ангины', 
            'бронхит', 'бронхита', 'бронхитом', 'пневмония', 'пневмонии', 'пневмонией',
            'отит', 'отита', 'отитом', 'гастрит', 'гастрита', 'гастритом', 
            'язва', 'язвы', 'язвой', 'язву',
            'кашел', 'кашля', 'кашляю', 'кашляет', 'насморк', 'чихан', 'чихаю',
            'недомогаю', 'недомогание', 'чувствую себя плохо', 'плохо себя чувствую',
            'головн', 'голова', 'головой', 'голову', 'горло', 'горла', 'горлом', 'горле',
            'ухо', 'уха', 'ухом', 'уши', 'ушей', 'ушах', 'кожа', 'кожи', 'кожей', 'коже',
            'нога', 'ноги', 'ногой', 'ногу', 'ног', 'ногами', 'колено', 'колена', 'коленом', 'колени', 'коленей',
            'рука', 'руки', 'рукой', 'руку', 'рук', 'руками', 'палец', 'пальца', 'пальцем', 'пальцы', 'пальцев',
            'плечо', 'плеча', 'плечом', 'плечи', 'плеч', 'спина', 'спины', 'спиной', 'спине', 'спину',
            'зуб', 'зуба', 'зубом', 'зубы', 'зубов', 'зубами', 'глаз', 'глаза', 'глазом', 'глазу', 'глаза', 'глаз',
            'сыпь', 'сыпи', 'сыпью', 'желудок', 'желудка', 'живот', 'живота',
            'сердц', 'сердечн', 'инфаркт', 'инфаркта', 'инфарктом', 'инсульт', 'инсульта', 'инсультом',
            'рак', 'рака', 'раком', 'опухоль', 'опухоли', 'опухолью', 'онкол', 'химиотерапи',
            'гепатит', 'гепатита', 'гепатитом', 'цирроз', 'почечн', 'почки', 'печен', 'печени',
            'операци', 'операция', 'операции', 'операцию', 'хирург', 'анестези', 'переливание', 'переливани',
            'сахар', 'диабет', 'астма', 'аллерги', 'артрит', 'артрита', 'артритом', 
            'остеохондроз', 'остеохондроза', 'остеохондрозом', 'ломит', 'поясниц', 
            'воспален', 'воспалена', 'воспалены', 'воспаление', 'воспалился', 'воспалилась', 'воспалилось', 'воспалились', 'отек',
            'опухоль', 'рана', 'раны', 'травма', 'травмы', 'перелом', 'переломил', 'переломила', 
            'сломал', 'сломала', 'сломали', 'слом', 'ушиб', 'ушибла', 'ушибли', 
            'порез', 'порезал', 'порезала', 'порезали', 'подвернул', 'подвернула', 'растяжение', 'растяжени',
            'беспокоит', 'беспокоят',
            'боль в', 'болит в', 'боли в', 'тошнит', 'тошнота', 'рвота', 'понос', 'диарея',
            'запор', 'слабость', 'слабый', 'усталость', 'устал', 'устала', 'бессонниц', 'депресси', 'стресс',
            'панич', 'тревож', 'мигрен', 'судорог', 'онемени', 'немеет', 'головокружен', 'кружится',
            'обморок', 'кровотечени', 'кровь', 'кровит', 'гной', 'выделени', 'отравлени', 'отравился'
        ]
        
        disclaimer = "\n\n⚠️ Я ИИ, а не врач. Пожалуйста, проконсультируйтесь со специалистом."
        needs_disclaimer = any(keyword in user_message.lower() or keyword in assistant_message.lower() 
                               for keyword in medical_keywords)
        
        if needs_disclaimer and disclaimer not in assistant_message:
            assistant_message += disclaimer
        
        session['chat_history'].append({
            'role': 'assistant',
            'content': assistant_message,
            'timestamp': datetime.now().strftime('%H:%M')
        })
        
        session.modified = True
        
        return jsonify({
            'response': assistant_message,
            'timestamp': datetime.now().strftime('%H:%M')
        })
    
    except Exception as e:
        return jsonify({'error': f'Ошибка: {str(e)}'}), 500

def handle_profile_command(message):
    parts = message.split()
    
    if len(parts) == 1:
        profile = session.get('profile', {})
        if any(profile.values()):
            profile_text = "👤 Ваш профиль:\n\n"
            if profile.get('age'):
                profile_text += f"• Возраст: {profile['age']}\n"
            if profile.get('gender'):
                profile_text += f"• Пол: {profile['gender']}\n"
            if profile.get('health_stats'):
                profile_text += f"• Здоровье: {profile['health_stats']}\n"
            if profile.get('activity_level'):
                profile_text += f"• Активность: {profile['activity_level']}\n"
            if profile.get('goals'):
                profile_text += f"• Цели: {', '.join(profile['goals'])}\n"
            if profile.get('allergies'):
                profile_text += f"• Аллергии: {', '.join(profile['allergies'])}\n"
            
            response_text = profile_text + "\n💡 Команды:\n"
            response_text += '<span class="command-example" data-command="/profile set age 25">/profile set age [возраст]</span>\n'
            response_text += '<span class="command-example" data-command="/profile set gender мужской">/profile set gender [пол]</span>\n'
            response_text += '<span class="command-example" data-command="/profile set activity средняя">/profile set activity [низкая|средняя|высокая]</span>\n'
            response_text += '<span class="command-example" data-command="/profile add goal похудеть">/profile add goal [цель]</span>\n'
            response_text += '<span class="command-example" data-command="/profile add allergy молоко">/profile add allergy [продукт]</span>'
        else:
            response_text = 'Профиль не настроен.\n💡 Начните с:\n<span class="command-example" data-command="/profile set age 25">/profile set age [возраст]</span>'
    
    elif len(parts) >= 3:
        action = parts[1]
        field = parts[2]
        value = ' '.join(parts[3:]) if len(parts) > 3 else ''
        
        if not value:
            response_text = f"❌ Ошибка: не указано значение для поля '{field}'\n\n"
            response_text += f"💡 Правильный формат:\n<code>/profile {action} {field} [значение]</code>\n\n"
            response_text += f'Пример правильной команды:\n<span class="command-example" data-command="/profile set age 25">/profile set age 25</span>'
        elif action == 'set':
            if field == 'age':
                try:
                    age_val = int(value)
                    if age_val < 1 or age_val > 120:
                        response_text = f"❌ Ошибка: возраст должен быть от 1 до 120 лет\n"
                        response_text += f"Вы указали: {value}\n\n"
                        response_text += f'💡 Пример правильной команды:\n<span class="command-example" data-command="/profile set age 25">/profile set age 25</span>'
                    else:
                        session['profile']['age'] = value
                        response_text = f"✅ Возраст установлен: {value}"
                except ValueError:
                    response_text = f"❌ Ошибка: возраст должен быть числом\n"
                    response_text += f"Вы указали: {value}\n\n"
                    response_text += f'💡 Пример правильной команды:\n<span class="command-example" data-command="/profile set age 25">/profile set age 25</span>'
            elif field == 'gender':
                session['profile']['gender'] = value
                response_text = f"✅ Пол установлен: {value}"
            elif field == 'activity':
                session['profile']['activity_level'] = value
                response_text = f"✅ Уровень активности: {value}"
            else:
                response_text = f"❌ Ошибка: неизвестное поле '{field}'\n\n"
                response_text += "Доступные поля:\n• age (возраст)\n• gender (пол)\n• activity (активность)\n\n"
                response_text += '💡 Пример правильной команды:\n<span class="command-example" data-command="/profile set age 25">/profile set age 25</span>'
        
        elif action == 'add':
            if field == 'goal':
                if 'goals' not in session['profile']:
                    session['profile']['goals'] = []
                session['profile']['goals'].append(value)
                response_text = f"✅ Цель добавлена: {value}"
            elif field == 'allergy':
                if 'allergies' not in session['profile']:
                    session['profile']['allergies'] = []
                session['profile']['allergies'].append(value)
                response_text = f"✅ Аллергия добавлена: {value}"
            else:
                response_text = f"❌ Ошибка: неизвестное поле '{field}'\n\n"
                response_text += 'Используйте:\n<span class="command-example" data-command="/profile add goal похудеть">/profile add goal [цель]</span>\n'
                response_text += '<span class="command-example" data-command="/profile add allergy молоко">/profile add allergy [продукт]</span>'
        else:
            response_text = f"❌ Ошибка: неизвестное действие '{action}'\n\n"
            response_text += "Используйте:\n• <code>set</code> - установить значение\n• <code>add</code> - добавить в список\n\n"
            response_text += '💡 Пример правильной команды:\n<span class="command-example" data-command="/profile set age 25">/profile set age 25</span>'
        
        session.modified = True
    else:
        response_text = "❌ Ошибка: неверный формат команды\n\n"
        response_text += '💡 Примеры использования:\n<span class="command-example" data-command="/profile">/profile</span> - показать профиль\n'
        response_text += '<span class="command-example" data-command="/profile set age 25">/profile set age 25</span>\n'
        response_text += '<span class="command-example" data-command="/profile add goal похудеть">/profile add goal похудеть</span>'
    
    session['chat_history'].append({
        'role': 'assistant',
        'content': response_text,
        'timestamp': datetime.now().strftime('%H:%M')
    })
    
    session.modified = True
    
    return jsonify({
        'response': response_text,
        'timestamp': datetime.now().strftime('%H:%M')
    })

def calculate_next_medication_time(med_time):
    """Вычисляет следующее время приема лекарства"""
    now = datetime.now()
    med_hour, med_minute = map(int, med_time.split(':'))
    
    next_time = now.replace(hour=med_hour, minute=med_minute, second=0, microsecond=0)
    
    if next_time <= now:
        next_time += timedelta(days=1)
    
    time_until = next_time - now
    hours_until = int(time_until.total_seconds() // 3600)
    minutes_until = int((time_until.total_seconds() % 3600) // 60)
    
    return {
        'next_datetime': next_time,
        'next_date': next_time.strftime('%d.%m.%Y'),
        'next_time': next_time.strftime('%H:%M'),
        'hours_until': hours_until,
        'minutes_until': minutes_until
    }

def handle_medication_command(message):
    parts = message.split(maxsplit=3)
    
    if len(parts) == 1:
        if session.get('medications'):
            med_text = "💊 Ваши лекарства:\n\n"
            for idx, med in enumerate(session['medications'], 1):
                next_info = calculate_next_medication_time(med['time'])
                time_str = ""
                if next_info['hours_until'] > 0:
                    time_str = f"через {next_info['hours_until']}ч {next_info['minutes_until']}мин"
                else:
                    time_str = f"через {next_info['minutes_until']} мин"
                
                med_text += f"{idx}. <strong>{med['name']}</strong> - {med['time']}\n"
                med_text += f"   📅 Следующий прием: {next_info['next_date']} ({time_str})\n\n"
            med_text += '💡 Удалить: <span class="command-example" data-command="/medication remove 1">/medication remove [номер]</span>'
            response_text = med_text
        else:
            response_text = "Нет лекарств.\n\n"
            response_text += '💡 Добавьте:\n<span class="command-example" data-command="/medication Аспирин 09:00">/medication [название] [время]</span>\n\n'
            response_text += 'Пример:\n<span class="command-example" data-command="/medication Витамин_D 09:00">/medication Витамин_D 09:00</span>'
    
    elif parts[1] == 'remove' and len(parts) >= 3:
        try:
            index = int(parts[2]) - 1
            if 'medications' in session and 0 <= index < len(session['medications']):
                removed = session['medications'].pop(index)
                session.modified = True
                response_text = f"✅ Удалено: {removed['name']}"
            else:
                response_text = f"❌ Ошибка: неверный номер лекарства\n"
                response_text += f"Вы указали: {parts[2]}\n"
                response_text += f"У вас всего {len(session.get('medications', []))} лекарств(а)\n\n"
                response_text += '💡 Посмотреть список: <span class="command-example" data-command="/medication">/medication</span>'
        except ValueError:
            response_text = f"❌ Ошибка: номер должен быть числом\n"
            response_text += f"Вы указали: {parts[2]}\n\n"
            response_text += '💡 Пример правильной команды:\n<span class="command-example" data-command="/medication remove 1">/medication remove 1</span>'
    
    else:
        if len(parts) < 3:
            response_text = f"❌ Ошибка: неполная команда\n\n"
            response_text += '💡 Правильный формат:\n<span class="command-example" data-command="/medication Аспирин 09:00">/medication [название] [время]</span>\n\n'
            response_text += 'Пример:\n<span class="command-example" data-command="/medication Аспирин 09:00">/medication Аспирин 09:00</span>'
        else:
            med_name = parts[1] if len(parts) > 1 else 'Лекарство'
            med_time = parts[2] if len(parts) > 2 else '09:00'
            
            if not validate_time(med_time):
                response_text = f"❌ Ошибка: неверный формат времени\n"
                response_text += f"Вы указали: <strong>{med_time}</strong>\n\n"
                response_text += "💡 Используйте формат ЧЧ:ММ (например: 09:00, 14:30, 21:15)\n\n"
                response_text += f'Пример правильной команды:\n<span class="command-example" data-command="/medication {med_name} 09:00">/medication {med_name} 09:00</span>'
            else:
                if 'medications' not in session:
                    session['medications'] = []
                
                next_info = calculate_next_medication_time(med_time)
                
                session['medications'].append({
                    'name': med_name.replace('_', ' '),
                    'time': med_time,
                    'created': datetime.now().isoformat()
                })
                session.modified = True
                
                time_str = ""
                if next_info['hours_until'] > 0:
                    time_str = f"через {next_info['hours_until']}ч {next_info['minutes_until']}мин"
                else:
                    time_str = f"через {next_info['minutes_until']} мин"
                
                response_text = f"✅ Добавлено: <strong>{med_name.replace('_', ' ')}</strong> в {med_time}\n\n📅 Следующий прием: {next_info['next_date']} ({time_str})"
    
    session['chat_history'].append({
        'role': 'assistant',
        'content': response_text,
        'timestamp': datetime.now().strftime('%H:%M')
    })
    
    session.modified = True
    
    return jsonify({
        'response': response_text,
        'timestamp': datetime.now().strftime('%H:%M')
    })

def handle_reminder_command(message):
    parts = message.split(maxsplit=4)
    
    if len(parts) == 1:
        if session.get('reminders'):
            rem_text = "⏰ Ваши напоминания:\n\n"
            for idx, rem in enumerate(session['reminders'], 1):
                rem_text += f"{idx}. {rem['text']} - {rem['time']}\n"
                if rem.get('repeat'):
                    rem_text += f"   Повтор: {rem['repeat']}\n"
            rem_text += '\n💡 Удалить: <span class="command-example" data-command="/reminder remove 1">/reminder remove [номер]</span>'
            response_text = rem_text
        else:
            response_text = "Нет напоминаний.\n\n"
            response_text += '💡 Добавьте: <span class="command-example" data-command="/reminder Попить_воды 10:00 ежедневно">/reminder [текст] [время] [повтор]</span>\n'
            response_text += 'Пример: <span class="command-example" data-command="/reminder Попить_воды 10:00 ежедневно">/reminder Попить_воды 10:00 ежедневно</span>'
    
    elif parts[1] == 'remove' and len(parts) >= 3:
        try:
            index = int(parts[2]) - 1
            if 'reminders' in session and 0 <= index < len(session['reminders']):
                removed = session['reminders'].pop(index)
                session.modified = True
                response_text = f"✅ Удалено напоминание: {removed['text']}"
            else:
                response_text = f"❌ Ошибка: неверный номер напоминания\n"
                response_text += f"У вас всего {len(session.get('reminders', []))} напоминаний\n\n"
                response_text += '💡 Пример правильной команды:\n<span class="command-example" data-command="/reminder remove 1">/reminder remove 1</span>'
        except ValueError:
            response_text = f"❌ Ошибка: номер должен быть числом\n"
            response_text += f"Вы указали: {parts[2]}\n\n"
            response_text += '💡 Пример правильной команды:\n<span class="command-example" data-command="/reminder remove 1">/reminder remove 1</span>'
    
    else:
        rem_text = parts[1] if len(parts) > 1 else 'Напоминание'
        rem_time = parts[2] if len(parts) > 2 else '10:00'
        rem_repeat = parts[3] if len(parts) > 3 else 'один раз'
        
        if not validate_time(rem_time):
            response_text = f"❌ Ошибка: неверный формат времени\n"
            response_text += f"Вы указали: {rem_time}\n\n"
            response_text += "💡 Используйте формат ЧЧ:ММ (например: 10:00, 14:30)\n\n"
            response_text += f'Пример правильной команды:\n<span class="command-example" data-command="/reminder {rem_text} 10:00 ежедневно">/reminder {rem_text} 10:00 ежедневно</span>'
        else:
            if 'reminders' not in session:
                session['reminders'] = []
            
            session['reminders'].append({
                'text': rem_text.replace('_', ' '),
                'time': rem_time,
                'repeat': rem_repeat,
                'created': datetime.now().isoformat()
            })
            session.modified = True
            
            response_text = f"✅ Напоминание создано: {rem_text.replace('_', ' ')} в {rem_time}"
            if rem_repeat != 'один раз':
                response_text += f" ({rem_repeat})"
    
    session['chat_history'].append({
        'role': 'user',
        'content': message,
        'timestamp': datetime.now().strftime('%H:%M')
    })
    
    session['chat_history'].append({
        'role': 'assistant',
        'content': response_text,
        'timestamp': datetime.now().strftime('%H:%M')
    })
    
    session.modified = True
    
    return jsonify({
        'response': response_text,
        'timestamp': datetime.now().strftime('%H:%M')
    })

def handle_knowledge_command(message):
    parts = message.split(maxsplit=1)
    
    if len(parts) == 1:
        response_text = "📚 База знаний по здоровью:\n\n"
        response_text += "Доступные категории:\n"
        for category in HEALTH_KNOWLEDGE_BASE.keys():
            response_text += f"• {category}\n"
        response_text += "\n💡 Используйте: /knowledge [категория]\nПример: /knowledge питание"
    else:
        category = parts[1].lower()
        
        if category in HEALTH_KNOWLEDGE_BASE:
            response_text = f"📚 {category.upper()}:\n\n"
            for topic, info in HEALTH_KNOWLEDGE_BASE[category].items():
                response_text += f"• {topic.upper()}: {info}\n\n"
        else:
            response_text = f"❌ Категория '{category}' не найдена.\nДоступные: {', '.join(HEALTH_KNOWLEDGE_BASE.keys())}"
    
    session['chat_history'].append({
        'role': 'user',
        'content': message,
        'timestamp': datetime.now().strftime('%H:%M')
    })
    
    session['chat_history'].append({
        'role': 'assistant',
        'content': response_text,
        'timestamp': datetime.now().strftime('%H:%M')
    })
    
    session.modified = True
    
    return jsonify({
        'response': response_text,
        'timestamp': datetime.now().strftime('%H:%M')
    })

def suggest_command(message):
    """Предлагает команду на основе естественного языка"""
    message_lower = message.lower()
    
    # Паттерны для напоминаний о лекарствах
    medication_patterns = [
        'напомни', 'напоминание', 'принять лекарство', 'принять таблетк', 
        'выпить лекарство', 'таблетк', 'лекарств', 'препарат', 'медикамент'
    ]
    
    # Паттерны для обычных напоминаний
    reminder_patterns = [
        'напомни мне', 'поставь напоминание', 'создай напоминание',
        'попить вод', 'сделать зарядк', 'прогулк'
    ]
    
    # Паттерны для профиля
    profile_patterns = [
        'мне ', 'лет', 'возраст', 'мой возраст', 'я ', 'мужчин', 'женщин',
        'хочу похудеть', 'моя цель', 'у меня аллерги', 'аллергия на'
    ]
    
    # Проверяем на лекарства
    if any(pattern in message_lower for pattern in medication_patterns):
        # Пытаемся извлечь время
        time_match = re.search(r'(\d{1,2}):(\d{2})', message)
        if time_match:
            suggested_time = time_match.group(0)
        else:
            # Ищем "через X минут/часов"
            through_match = re.search(r'через (\d+) (минут|час)', message_lower)
            if through_match:
                amount = int(through_match.group(1))
                unit = through_match.group(2)
                if 'час' in unit:
                    future_time = datetime.now() + timedelta(hours=amount)
                else:
                    future_time = datetime.now() + timedelta(minutes=amount)
                suggested_time = future_time.strftime('%H:%M')
            else:
                suggested_time = "09:00"
        
        # Пытаемся извлечь название лекарства
        med_words = message.split()
        possible_name = "Лекарство"
        for i, word in enumerate(med_words):
            if any(p in word.lower() for p in ['лекарств', 'таблетк', 'препарат']):
                if i + 1 < len(med_words):
                    possible_name = med_words[i + 1].capitalize()
                break
        
        return f"""💡 Похоже, вы хотите добавить напоминание о лекарстве!

Используйте команду:
<span class="command-example" data-command="/medication {possible_name} {suggested_time}">/medication {possible_name} {suggested_time}</span>

Пример:
<span class="command-example" data-command="/medication Аспирин {suggested_time}">/medication Аспирин {suggested_time}</span>

📝 Формат: /medication [название] [время_HH:MM]"""
    
    # Проверяем на обычные напоминания
    elif any(pattern in message_lower for pattern in reminder_patterns):
        time_match = re.search(r'(\d{1,2}):(\d{2})', message)
        if time_match:
            suggested_time = time_match.group(0)
        else:
            through_match = re.search(r'через (\d+) (минут|час)', message_lower)
            if through_match:
                amount = int(through_match.group(1))
                unit = through_match.group(2)
                if 'час' in unit:
                    future_time = datetime.now() + timedelta(hours=amount)
                else:
                    future_time = datetime.now() + timedelta(minutes=amount)
                suggested_time = future_time.strftime('%H:%M')
            else:
                suggested_time = "10:00"
        
        # Извлекаем текст напоминания
        reminder_text = message.replace('напомни мне', '').replace('напомни', '').strip()
        if not reminder_text or len(reminder_text) < 3:
            reminder_text = "Попить_воды"
        else:
            reminder_text = reminder_text.split()[0].capitalize()
        
        return f"""💡 Используйте команду для создания напоминания:

<span class="command-example" data-command="/reminder {reminder_text} {suggested_time} ежедневно">/reminder {reminder_text} {suggested_time} ежедневно</span>

📝 Формат: /reminder [текст] [время_HH:MM] [повтор]
Повтор может быть: ежедневно, еженедельно, один_раз"""
    
    # Проверяем на профиль
    elif any(pattern in message_lower for pattern in profile_patterns):
        age_match = re.search(r'(\d{1,3})\s*(лет|года|год)', message_lower)
        suggested_age = age_match.group(1) if age_match else "25"
        
        return f"""💡 Для настройки профиля используйте команды:

<span class="command-example" data-command="/profile set age {suggested_age}">/profile set age {suggested_age}</span>
<span class="command-example" data-command="/profile set gender мужской">/profile set gender [мужской/женский]</span>
<span class="command-example" data-command="/profile set activity средняя">/profile set activity [низкая/средняя/высокая]</span>
<span class="command-example" data-command="/profile add goal похудеть">/profile add goal [ваша_цель]</span>

Пример:
<span class="command-example" data-command="/profile set age {suggested_age}">/profile set age {suggested_age}</span>"""
    
    return None

def validate_time(time_str):
    """Валидация формата времени HH:MM"""
    try:
        parts = time_str.split(':')
        if len(parts) != 2:
            return False
        
        hours = int(parts[0])
        minutes = int(parts[1])
        
        if hours < 0 or hours > 23:
            return False
        if minutes < 0 or minutes > 59:
            return False
        
        return True
    except (ValueError, AttributeError):
        return False

def search_knowledge_base(query):
    """Поиск релевантной информации в базе знаний"""
    query_lower = query.lower()
    relevant_info = []
    
    for category, topics in HEALTH_KNOWLEDGE_BASE.items():
        for topic, info in topics.items():
            if topic in query_lower or category in query_lower:
                relevant_info.append(f"{topic}: {info}")
    
    return " ".join(relevant_info[:3]) if relevant_info else ""

@app.route('/get_history')
def get_history():
    return jsonify({
        'history': session.get('chat_history', []),
        'medications': session.get('medications', []),
        'reminders': session.get('reminders', [])
    })

@app.route('/get_reminders')
def get_reminders():
    """Получить активные напоминания на текущее время и удалить одноразовые"""
    current_time = datetime.now().strftime('%H:%M')
    active_reminders = []
    
    # Проверяем напоминания
    if 'reminders' in session:
        reminders_to_remove = []
        for idx, reminder in enumerate(session.get('reminders', [])):
            if reminder['time'] == current_time:
                active_reminders.append(reminder)
                # Если напоминание одноразовое, помечаем для удаления
                if reminder.get('repeat', 'один раз').lower() in ['один раз', 'однократно', 'разовое']:
                    reminders_to_remove.append(idx)
        
        # Удаляем одноразовые напоминания (с конца, чтобы индексы не сбивались)
        for idx in sorted(reminders_to_remove, reverse=True):
            session['reminders'].pop(idx)
        
        if reminders_to_remove:
            session.modified = True
    
    # Проверяем лекарства
    for medication in session.get('medications', []):
        if medication['time'] == current_time:
            active_reminders.append({
                'text': f"Принять {medication['name']}",
                'time': medication['time'],
                'type': 'medication'
            })
    
    return jsonify({'reminders': active_reminders})

@app.route('/get_medication_schedule')
def get_medication_schedule():
    """Получить отсортированное расписание приема лекарств"""
    medications = session.get('medications', [])
    schedule = []
    
    for med in medications:
        next_info = calculate_next_medication_time(med['time'])
        schedule.append({
            'name': med['name'],
            'time': med['time'],
            'next_datetime': next_info['next_datetime'],
            'next_date': next_info['next_date'],
            'hours_until': next_info['hours_until'],
            'minutes_until': next_info['minutes_until']
        })
    
    # Сортируем по времени до следующего приема
    schedule.sort(key=lambda x: x['next_datetime'])
    
    return jsonify({'schedule': schedule})

@app.route('/clear_profile', methods=['POST'])
def clear_profile():
    """Очистить профиль пользователя"""
    session['profile'] = {
        'age': None,
        'gender': None,
        'health_stats': None,
        'goals': [],
        'allergies': [],
        'activity_level': None
    }
    session.modified = True
    return jsonify({'status': 'success'})

@app.route('/delete_reminder', methods=['POST'])
def delete_reminder():
    """Удалить напоминание по индексу"""
    if not request.json:
        return jsonify({'error': 'Некорректный запрос'}), 400
    
    index = request.json.get('index')
    reminder_type = request.json.get('type', 'reminder')
    
    try:
        index = int(index)
        if reminder_type == 'medication':
            if 'medications' in session and 0 <= index < len(session['medications']):
                removed = session['medications'].pop(index)
                session.modified = True
                return jsonify({'status': 'success', 'message': f"Удалено: {removed['name']}"})
        else:
            if 'reminders' in session and 0 <= index < len(session['reminders']):
                removed = session['reminders'].pop(index)
                session.modified = True
                return jsonify({'status': 'success', 'message': f"Удалено: {removed['text']}"})
        
        return jsonify({'error': 'Неверный индекс'}), 400
    except (ValueError, KeyError):
        return jsonify({'error': 'Ошибка при удалении'}), 400

@app.route('/clear_chat', methods=['POST'])
def clear_chat():
    session['chat_history'] = []
    session.modified = True
    return jsonify({'status': 'success'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
