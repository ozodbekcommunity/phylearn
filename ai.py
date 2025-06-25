from groq import Groq
from config import settings
import json
from typing import Dict, Any, Optional

class AIAssistant:
    def __init__(self):
        if not settings.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY sozlamalarda o'rnatilmagan")
        self.client = Groq(api_key=settings.GROQ_API_KEY)
        self.model = settings.GROQ_MODEL
    
    async def evaluate_text_answer(self, question: str, student_answer: str, correct_answer: str = None) -> Dict[str, Any]:
        """
        AI yordamida talaba matnli javobini baholash
        """
        prompt = f"""
Sen elektr va magnetizm bo'yicha mutaxassislik qilgan ekspert fizika o'qituvchisisiz.

TOPSHIRIQ: Talabaning fizika savoliga javobini baholab ber.

SAVOL: {question}

TALABA JAVOBI: {student_answer}

{f"NAMUNA JAVOB: {correct_answer}" if correct_answer else ""}

BAHOLASH MEZONLARI:
1. Fizika tushunchalarining to'g'riligi (40%)
2. Javobning to'liqligi (30%)
3. Mantiqiy bayon (20%)
4. Terminologiya ishlatilishi (10%)

JAVOB TALABLARI:
- 0 dan 100 gacha baho ber
- Bahoni tushuntir
- Xato va kamchiliklarni ko'rsat
- Yaxshilash uchun tavsiyalar ber

Qat'iyan JSON formatida javob ber:
{{
    "score": 0 dan 100 gacha son,
    "feedback": "batafsil fikr-mulohaza",
    "strengths": ["javobning kuchli tomonlari"],
    "weaknesses": ["zaif tomonlar va xatolar"],
    "recommendations": ["yaxshilash uchun tavsiyalar"]
}}
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000
            )
            
            result = json.loads(response.choices[0].message.content)
            return result
            
        except Exception as e:
            return {
                "score": 0,
                "feedback": f"Javobni qayta ishlashda xatolik: {str(e)}",
                "strengths": [],
                "weaknesses": ["Tekshirishda texnik xatolik"],
                "recommendations": ["Qo'lda tekshirish uchun o'qituvchiga murojaat qiling"]
            }
    
    async def generate_hint(self, question: str, student_attempts: list) -> str:
        """
        Talaba uchun maslahat yaratish
        """
        attempts_text = "\n".join([f"{i+1}-urinish: {attempt}" for i, attempt in enumerate(student_attempts)])
        
        prompt = f"""
Sen do'stona yordamchi-o'qituvchi fizika bo'yicha.

SAVOL: {question}

TALABANING OLDINGI URINISHLARI:
{attempts_text}

To'g'ri javobni bermay, talabani to'g'ri yo'lga yo'naltiruvchi qisqa, lekin foydali maslahat ber. 
Oddiy til va rag'batlantiruvchi ohangda yoz.
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=200
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            return "Afsuski, maslahat yarata olmadim. Yana urinib ko'ring yoki o'qituvchiga murojaat qiling."
    
    async def analyze_learning_progress(self, student_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Talabaning o'rganish jarayonini tahlil qilish
        """
        prompt = f"""
Talaba jarayoni ma'lumotlarini tahlil qilib, tavsiyalar ber.

TALABA MA'LUMOTLARI: {json.dumps(student_data, ensure_ascii=False, indent=2)}

JSON formatida javob ber:
{{
    "overall_assessment": "umumiy jarayon bahosi",
    "strengths": ["kuchli tomonlar"],
    "areas_for_improvement": ["yaxshilanishi kerak bo'lgan sohalar"],
    "recommendations": ["aniq tavsiyalar"],
    "predicted_success_rate": 0 dan 100 gacha son
}}
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=800
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            return {
                "overall_assessment": "Tahlil xatoligi",
                "strengths": [],
                "areas_for_improvement": [],
                "recommendations": ["Shaxsiy maslahat uchun o'qituvchiga murojaat qiling"],
                "predicted_success_rate": 0
            }

# Global misolda
ai_assistant = AIAssistant() if settings.GROQ_API_KEY else None