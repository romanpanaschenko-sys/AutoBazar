# ==================== API ДЛЯ MINI APP ====================
async def api_ads(request):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Используем COALESCE, чтобы заменить null на безопасные значения по умолчанию
        cursor.execute("""
            SELECT 
                id, 
                COALESCE(name, 'Без названия'), 
                COALESCE(oem, 'нет'), 
                COALESCE(price, '0'), 
                COALESCE(category, 'Другое'), 
                user_id, 
                photo_id, 
                COALESCE(car_info, 'Авто не указано') 
            FROM ads ORDER BY id DESC LIMIT 50
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        ads_list = []
        for row in rows:
            photo_url = ""
            if row[6]: # Если есть photo_id
                try:
                    photo_url = f"https://autobazar-a22p.onrender.com/api/photo/{row[6]}"
                except Exception as e:
                    logging.error(f"Ошибка загрузки фото: {e}")

            ads_list.append({
                "id": row[0],
                "name": row[1],
                "oem": row[2],
                "price": row[3],
                "category": row[4],
                "user_id": row[5] if row[5] else 0, # Защита от пустого ID
                "photo_url": photo_url,
                "car_info": row[7]
            })
        
        return web.json_response(ads_list, headers={'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500, headers={'Access-Control-Allow-Origin': '*'})
