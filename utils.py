import os
import aiofiles
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
import qrcode
from io import BytesIO
import requests
from datetime import datetime
from typing import Optional, List
import uuid
import json
from config import settings

# PDF konvertatsiya qilish uchun import
try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False
    print("⚠️ pdf2image o'rnatilmagan. PDF fayllar rasmlarga konvertatsiya qilinmaydi.")

from PIL import Image as PILImage

class FileManager:
    @staticmethod
    def ensure_upload_dir():
        """Yuklash uchun katalog yaratish"""
        upload_path = settings.UPLOAD_DIR
        os.makedirs(upload_path, exist_ok=True)
        
        # Pastki kataloglarni yaratish
        subdirs = ['pdfs', 'solutions', 'certificates', 'temp', 'images', 'pdf_pages']
        for subdir in subdirs:
            os.makedirs(os.path.join(upload_path, subdir), exist_ok=True)
    
    @staticmethod
    async def save_upload_file(file, subdir: str = "") -> str:
        """Yuklangan faylni saqlash"""
        FileManager.ensure_upload_dir()
        
        # Noyob fayl nomini yaratish
        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        
        # Yo'lni to'g'ri shakllantirish
        if subdir:
            file_path = os.path.join(settings.UPLOAD_DIR, subdir, unique_filename)
            url_path = f"{settings.UPLOAD_DIR}/{subdir}/{unique_filename}"
        else:
            file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)
            url_path = f"{settings.UPLOAD_DIR}/{unique_filename}"
        
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        # Agar bu PDF fayl bo'lsa, rasmlarga konvertatsiya qilamiz
        if file_extension.lower() == '.pdf':
            print(f"📄 PDF ni rasmlarga konvertatsiya qilish: {file_path}")
            images_info = FileManager.convert_pdf_to_images(file_path, unique_filename)
            if images_info:
                print(f"✅ PDF {len(images_info['pages'])} ta rasmga konvertatsiya qilindi")
                # PDF yo'li o'rniga rasmlar haqida ma'lumot qaytaramiz
                return json.dumps(images_info)
        
        return url_path
    
    @staticmethod
    def convert_pdf_to_images(pdf_path: str, pdf_filename: str) -> Optional[dict]:
        """PDF ni rasmlarga konvertatsiya qilish"""
        if not PDF2IMAGE_AVAILABLE:
            print("❌ pdf2image mavjud emas")
            return None
        
        try:
            # Ushbu PDF sahifalari uchun papka yaratamiz
            pdf_name = os.path.splitext(pdf_filename)[0]
            pages_dir = os.path.join(settings.UPLOAD_DIR, 'pdf_pages', pdf_name)
            os.makedirs(pages_dir, exist_ok=True)
            
            print(f"🔄 PDF konvertatsiyasi: {pdf_path}")
            
            # PDF ni rasmlarga konvertatsiya qilamiz
            images = convert_from_path(
                pdf_path, 
                dpi=150,  # Yaxshi sifat
                output_folder=pages_dir,
                fmt='PNG',
                thread_count=2
            )
            
            # Har bir sahifani saqlaymiz
            page_paths = []
            for i, image in enumerate(images):
                page_filename = f"page_{i+1:03d}.png"
                page_path = os.path.join(pages_dir, page_filename)
                
                # Rasm o'lchamini optimallashtirish
                # Veb uchun kenglikni 1200px ga cheklaymiz
                if image.width > 1200:
                    ratio = 1200 / image.width
                    new_size = (1200, int(image.height * ratio))
                    image = image.resize(new_size, PILImage.Resampling.LANCZOS)
                
                image.save(page_path, 'PNG', optimize=True)
                
                # Veb uchun URL yo'li
                page_url = f"uploads/pdf_pages/{pdf_name}/{page_filename}"
                page_paths.append({
                    "page": i + 1,
                    "url": page_url,
                    "path": page_path
                })
                
                print(f"  📄 {i+1}-sahifa saqlandi: {page_filename}")
            
            # Asl PDF ni o'chirish (ixtiyoriy)
            # os.remove(pdf_path)
            
            return {
                "type": "pdf_images",
                "total_pages": len(page_paths),
                "pages": page_paths,
                "pdf_name": pdf_name
            }
            
        except Exception as e:
            print(f"❌ PDF konvertatsiyasida xatolik: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    @staticmethod
    def validate_file(file) -> bool:
        """Yuklanadigan faylni tekshirish"""
        if file.size > settings.MAX_FILE_SIZE:
            return False
        
        file_extension = os.path.splitext(file.filename)[1].lower()
        return file_extension in settings.ALLOWED_EXTENSIONS

class CertificateGenerator:
    @staticmethod
    def generate_certificate(student_name: str, course_name: str, completion_date: datetime, 
                           score: float, certificate_id: str) -> str:
        """PDF sertifikat yaratish"""
        FileManager.ensure_upload_dir()
        
        # Sertifikat fayli yo'li
        cert_filename = f"certificate_{certificate_id}.pdf"
        cert_path = os.path.join(settings.UPLOAD_DIR, 'certificates', cert_filename)
        
        # PDF yaratish
        doc = SimpleDocTemplate(cert_path, pagesize=A4)
        styles = getSampleStyleSheet()
        story = []
        
        # Sarlavha
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            alignment=1,  # Markazlashtirish
            textColor=colors.darkblue
        )
        
        # Tasdiqlash uchun QR kod
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(f"https://phylearn.com/verify/{certificate_id}")
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        
        # QR kodini vaqtinchalik faylga saqlash
        qr_path = os.path.join(settings.UPLOAD_DIR, 'temp', f'qr_{certificate_id}.png')
        qr_img.save(qr_path)
        
        # Sertifikat mazmuni
        story.append(Paragraph("KURS TUGATISH SERTIFIKATI", title_style))
        story.append(Spacer(1, 20))
        
        story.append(Paragraph(f"Shu narsa bilan tasdiqlanadi ki,", styles['Normal']))
        story.append(Spacer(1, 10))
        
        name_style = ParagraphStyle(
            'StudentName',
            parent=styles['Heading2'],
            fontSize=18,
            alignment=1,
            textColor=colors.darkgreen
        )
        story.append(Paragraph(student_name, name_style))
        story.append(Spacer(1, 20))
        
        story.append(Paragraph(f"kursni muvaffaqiyatli tugatdi:", styles['Normal']))
        story.append(Spacer(1, 10))
        story.append(Paragraph(course_name, styles['Heading3']))
        story.append(Spacer(1, 20))
        
        story.append(Paragraph(f"Yakuniy baho: {score:.1f}%", styles['Normal']))
        story.append(Paragraph(f"Tugatish sanasi: {completion_date.strftime('%d.%m.%Y')}", styles['Normal']))
        story.append(Paragraph(f"Sertifikat ID: {certificate_id}", styles['Normal']))
        story.append(Spacer(1, 30))
        
        # QR kod
        story.append(Image(qr_path, width=1*inch, height=1*inch))
        story.append(Spacer(1, 10))
        story.append(Paragraph("Tasdiqlash uchun skanerlang", styles['Normal']))
          # PDF ni qurish
        doc.build(story)
        
        # Vaqtinchalik QR faylini o'chirish
        os.remove(qr_path)
        
        # Faylga mutlaq yo'lni qaytarish
        return os.path.abspath(os.path.join(settings.UPLOAD_DIR, 'certificates', cert_filename))

class TelegramNotifier:
    @staticmethod
    async def send_completion_notification(telegram_id: str, student_name: str, 
                                         course_name: str, certificate_id: str):
        """Telegram ga xabar yuborish"""
        if not settings.TELEGRAM_BOT_TOKEN:
            return False
        
        message = f"""
🎉 Tabriklaymiz, {student_name}!

Siz kursni muvaffaqiyatli tugatdingiz: "{course_name}"

🏆 Sizning sertifikatingiz tayyor!
📋 Sertifikat ID: {certificate_id}

Sertifikat PhyLearn platformasidagi shaxsiy kabinetingizda mavjud.

Fizikani o'rganishda omad tilaymiz! ⚡🧲
        """
        
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": telegram_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        try:
            response = requests.post(url, json=data, timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"Telegram xabar yuborishda xatolik: {e}")
            return False

class ProgressCalculator:
    @staticmethod
    def calculate_module_progress(student_id: int, module_id: int) -> dict:
        """Modul bo'yicha jarayonni hisoblash"""
        from models import StudentProgress, Section
        
        # Modulning barcha bo'limlarini olish
        sections = Section.select().where(Section.module_id == module_id, Section.is_active == True)
        total_sections = sections.count()
        
        if total_sections == 0:
            return {"progress": 0, "completed": 0, "total": 0}
        
        # Talaba jarayonini olish
        completed_sections = StudentProgress.select().where(
            StudentProgress.student_id == student_id,
            StudentProgress.module_id == module_id,
            StudentProgress.status == 'completed'
        ).count()
        
        progress_percentage = (completed_sections / total_sections) * 100
        
        return {
            "progress": round(progress_percentage, 1),
            "completed": completed_sections,
            "total": total_sections
        }
    
    @staticmethod
    def calculate_overall_progress(student_id: int) -> dict:
        """Talabaning umumiy jarayonini hisoblash"""
        from models import StudentProgress, Module
        
        # Barcha faol modullarni olish
        modules = Module.select().where(Module.is_active == True)
        total_modules = modules.count()
        
        if total_modules == 0:
            return {"progress": 0, "completed_modules": 0, "total_modules": 0}
        
        completed_modules = 0
        total_score = 0
        
        for module in modules:
            module_progress = ProgressCalculator.calculate_module_progress(student_id, module.id)
            if module_progress["progress"] >= 80:  # Modul 80%+ jarayon bilan tugallangan hisoblanadi
                completed_modules += 1
            total_score += module_progress["progress"]
        
        overall_progress = total_score / total_modules if total_modules > 0 else 0
        
        return {
            "progress": round(overall_progress, 1),
            "completed_modules": completed_modules,
            "total_modules": total_modules,
            "average_score": round(overall_progress, 1)
        }

# Import qilinganda ishga tushirish
FileManager.ensure_upload_dir()