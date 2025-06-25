from fastapi import FastAPI, Request, Form, File, Response, UploadFile, Depends, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.sessions import SessionMiddleware
import uvicorn
from datetime import datetime, timedelta
import json
import os
from typing import Optional, List

# Mahalliy modullarni import qilish
from models import *
from config import settings
from ai import ai_assistant
from utils import FileManager, CertificateGenerator, TelegramNotifier, ProgressCalculator

# FastAPI ni ishga tushirish
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Fizikani o'rganish platformasi: elektr va magnetizm"
)

# Middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="session",
    max_age=3600,  # 1 hour
    same_site="lax",
    https_only=False  # Set to True in production with HTTPS
)

# Statik fayllar va shablonlar
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# class SPAStaticFiles(StaticFiles):
#     async def get_response(self, path: str, scope):
#         try:
#             return await super().get_response(path, scope)
#         except HTTPException as ex:
#             if ex.status_code == 404:
#                 print(f"Fayl topilmadi: {path}")
#             raise ex

# app.mount("/uploads", SPAStaticFiles(directory="uploads"), name="uploads")

# Ma'lumotlar bazasini ishga tushirish
create_tables()

# ===============================
# BOG'LIQLIKLAR VA YORDAMCHILAR
# ===============================

def get_current_user(request: Request) -> Optional[User]:
    """Sessiyadan joriy foydalanuvchini olish"""
    user_id = request.session.get("user_id")
    if user_id:
        try:
            return User.get_by_id(user_id)
        except User.DoesNotExist:
            pass
    return None

def require_auth(request: Request) -> User:
    """Majburiy avtorizatsiya"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Avtorizatsiya kerak")
    return user

def require_admin(request: Request) -> User:
    """Majburiy administrator huquqlari"""
    try:
        user = require_auth(request)
        print(f"Checking admin access for user: {user.username}, Role: {user.role}")
        
        if not hasattr(user, 'role') or user.role != 'superadmin':
            print(f"Access denied. User role: {getattr(user, 'role', 'no role')}")
            raise HTTPException(status_code=403, detail="Administrator huquqlari kerak")
            
        return user
    except Exception as e:
        print(f"Admin access check failed: {str(e)}")
        raise

def check_section_access(student: User, section: Section) -> bool:
    """Bo'limga kirish huquqini tekshirish (tartib bo'yicha)"""
    if section.order_index == 1:
        return True
    
    # Oldingi bo'lim tugallanganligini tekshirish
    prev_section = Section.select().where(
        Section.module == section.module,
        Section.order_index == section.order_index - 1
    ).first()
    
    if prev_section:
        progress = StudentProgress.select().where(
            StudentProgress.student == student,
            StudentProgress.section == prev_section,
            StudentProgress.status == 'completed'
        ).first()
        return progress is not None
    
    return True

# ===============================
# MARSHRUTLAR - UMUMIY
# ===============================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Bosh sahifa"""
    user = get_current_user(request)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user
    })

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Kirish sahifasi"""
    if get_current_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": request.session.pop("error", None),
        "success": request.session.pop("success", None)
    })

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Kirishni qayta ishlash"""
    try:
        print(f"Login attempt for username/email: {username}")
        user = User.get((User.username == username) | (User.email == username))
        print(f"User found: {user.username}, Role: {user.role}, Active: {user.is_active}")
        
        if user.check_password(password):
            print("Password check passed")
            if user.is_active:
                request.session["user_id"] = user.id
                request.session["user_role"] = user.role
                user.last_login = datetime.now()
                user.save()
                
                print(f"Session after login: {dict(request.session)}")
                
                if user.role == 'superadmin':
                    print("Redirecting to /admin")
                    return RedirectResponse("/admin", status_code=302)
                else:
                    print("Redirecting to /dashboard")
                    return RedirectResponse("/dashboard", status_code=302)
            else:
                print("User account is not active")
                request.session["error"] = "Hisobingiz faol emas. Administrator bilan bog'laning."
        else:
            print("Invalid password")
            request.session["error"] = "Noto'g'ri login yoki parol"
    
    except User.DoesNotExist:
        print(f"User not found: {username}")
        request.session["error"] = "Foydalanuvchi topilmadi"
    except Exception as e:
        print(f"Login error: {str(e)}")
        request.session["error"] = f"Xatolik yuz berdi: {str(e)}"
    
    return RedirectResponse("/login", status_code=302)

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Ro'yxatdan o'tish sahifasi"""
    if get_current_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    
    return templates.TemplateResponse("register.html", {
        "request": request,
        "error": request.session.pop("error", None)
    })

@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    telegram_id: str = Form(None)
):
    """Ro'yxatdan o'tishni qayta ishlash"""
    try:
        # Noyoblikni tekshirish
        if User.select().where((User.username == username) | (User.email == email)).exists():
            request.session["error"] = "Bunday ism yoki email bilan foydalanuvchi allaqachon mavjud"
            return RedirectResponse("/register", status_code=302)
        
        # Foydalanuvchi yaratish
        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            telegram_id=telegram_id if telegram_id else None
        )
        user.set_password(password)
        user.save()
        
        request.session["success"] = "Ro'yxatdan o'tish muvaffaqiyatli! Tizimga kiring."
        return RedirectResponse("/login", status_code=302)
    
    except Exception as e:
        request.session["error"] = f"Ro'yxatdan o'tishda xatolik: {str(e)}"
        return RedirectResponse("/register", status_code=302)

@app.get("/logout")
async def logout(request: Request):
    """Chiqish"""
    request.session.clear()
    return RedirectResponse("/", status_code=302)

# ===============================
# MARSHRUTLAR - TALABA
# ===============================

@app.get("/dashboard", response_class=HTMLResponse)
async def student_dashboard(request: Request):
    """Talaba paneli"""
    user = require_auth(request)
    if user.role != 'student':
        return RedirectResponse("/admin", status_code=302)
    
    # Modullar va jarayonni olish
    modules = Module.select().where(Module.is_active == True).order_by(Module.order_index)
    
    modules_data = []
    for module in modules:
        progress = ProgressCalculator.calculate_module_progress(user.id, module.id)
        modules_data.append({
            "module": module,
            "progress": progress
        })
    
    # Umumiy jarayon
    overall_progress = ProgressCalculator.calculate_overall_progress(user.id)
    
    # So'nggi faoliyatlar
    recent_sessions = SessionData.select().where(
        SessionData.student == user
    ).order_by(SessionData.session_start.desc()).limit(5)
    
    return templates.TemplateResponse("student_dashboard.html", {
        "request": request,
        "user": user,
        "modules_data": modules_data,
        "overall_progress": overall_progress,
        "recent_sessions": recent_sessions
    })

@app.get("/module/{module_id}", response_class=HTMLResponse)
async def module_view(request: Request, module_id: int):
    """Modulni ko'rish"""
    user = require_auth(request)
    
    try:
        module = Module.get_by_id(module_id)
        sections = Section.select().where(
            Section.module == module,
            Section.is_active == True
        ).order_by(Section.order_index)
        
        # Bo'limlar bo'yicha jarayon
        sections_data = []
        for section in sections:
            progress = StudentProgress.select().where(
                StudentProgress.student == user,
                StudentProgress.section == section
            ).first()
            
            has_access = check_section_access(user, section)
            
            sections_data.append({
                "section": section,
                "progress": progress,
                "has_access": has_access
            })
        
        return templates.TemplateResponse("module_view.html", {
            "request": request,
            "user": user,
            "module": module,
            "sections_data": sections_data
        })
    
    except Module.DoesNotExist:
        raise HTTPException(status_code=404, detail="Modul topilmadi")

@app.get("/section/{section_id}", response_class=HTMLResponse)
async def section_view(request: Request, section_id: int):
    """Bo'limni ko'rish"""
    user = require_auth(request)
    
    try:
        section = Section.get_by_id(section_id)
        
        # Kirish huquqini tekshirish
        if not check_section_access(user, section):
            raise HTTPException(status_code=403, detail="Bo'limga kirish taqiqlanган")
        
        # Jarayonni olish yoki yaratish
        progress, created = StudentProgress.get_or_create(
            student=user,
            module=section.module,
            section=section,
            defaults={'status': 'in_progress', 'started_at': datetime.now()}
        )
        
        if created or progress.status == 'not_started':
            progress.status = 'in_progress'
            progress.started_at = datetime.now()
            progress.save()
        
        # Sessiya yaratish
        session = SessionData.create(
            student=user,
            section=section,
            session_start=datetime.now()
        )
        
        # Bo'lim turiga qarab ma'lumotlarni tayyorlash
        context = {
            "request": request,
            "user": user,
            "section": section,
            "progress": progress,
            "session": session
        }
        
        # Bo'lim mazmunini qayta ishlash
        content_data = None
        if section.content:
            try:
                content_data = json.loads(section.content)
            except:
                content_data = None
        
        context["content_data"] = content_data
        
        if section.section_type in ['theory', 'examples']:
            # PDF dan rasmlar bor-yo'qligini tekshirish
            if content_data and content_data.get('type') == 'pdf_images':
                return templates.TemplateResponse("section_images.html", context)
            else:
                # Eski PDF ko'ruvchiga qaytish
                return templates.TemplateResponse("section_pdf.html", context)
        
        elif section.section_type in ['tasks', 'control']:
            # Savollar bilan bo'limlar
            questions = Question.select().where(
                Question.section == section
            ).order_by(Question.order_index)
            
            # Talaba javoblarini olish
            student_answers = {}
            for question in questions:
                answer = StudentAnswer.select().where(
                    StudentAnswer.student == user,
                    StudentAnswer.question == question
                ).first()
                if answer:
                    student_answers[question.id] = answer
            
            context.update({
                "questions": questions,
                "student_answers": student_answers
            })
            return templates.TemplateResponse("section_questions.html", context)
        
        elif section.section_type == 'test':
            # Test
            questions = Question.select().where(
                Question.section == section
            ).order_by(Question.order_index)
            
            context["questions"] = questions
            return templates.TemplateResponse("section_test.html", context)
    
    except Section.DoesNotExist:
        raise HTTPException(status_code=404, detail="Bo'lim topilmadi")

@app.post("/section/{section_id}/complete")
async def complete_section(request: Request, section_id: int):
    """Bo'limni tugatish"""
    user = require_auth(request)
    
    try:
        section = Section.get_by_id(section_id)
        
        progress = StudentProgress.get(
            StudentProgress.student == user,
            StudentProgress.section == section
        )
        
        progress.status = 'completed'
        progress.completed_at = datetime.now()
        progress.save()
        
        # Sessiyani yopish
        active_session = SessionData.select().where(
            SessionData.student == user,
            SessionData.section == section,
            SessionData.session_end.is_null()
        ).order_by(SessionData.session_start.desc()).first()
        
        if active_session:
            active_session.session_end = datetime.now()
            active_session.duration = (active_session.session_end - active_session.session_start).seconds
            active_session.save()
        
        # Butun modulning tugallanganligini tekshirish
        await check_module_completion(user, section.module)
        
        return JSONResponse({"status": "success", "message": "Bo'lim tugallandi"})
    
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

async def check_module_completion(student: User, module: Module):
    """Modul tugallanganligini tekshirish va sertifikat berish"""
    progress = ProgressCalculator.calculate_module_progress(student.id, module.id)
    
    if progress["progress"] >= 80:  # Modul tugallangan hisoblanadi
        # Sertifikat allaqachon berilgan-berilmaganligini tekshirish
        existing_cert = Certificate.select().where(
            Certificate.student == student,
            Certificate.module == module
        ).first()
        
        if not existing_cert:            
            # Sertifikat yaratish
            certificate = Certificate(
                student=student,
                module=module,
                overall_score=progress["progress"]
            )
            certificate.certificate_id = certificate.generate_certificate_id()
            
            # PDF yaratish
            cert_path = CertificateGenerator.generate_certificate(
                student.full_name,
                module.title,
                datetime.now(),
                progress["progress"],
                certificate.certificate_id
            )
            
            # Sertifikatni fayl yo'li bilan saqlash
            certificate.file_path = cert_path
            certificate.save()
            
            # Telegram ga xabar yuborish
            if student.telegram_id:
                await TelegramNotifier.send_completion_notification(
                    student.telegram_id,
                    student.full_name,
                    module.title,
                    certificate.certificate_id
                )

        # Keyingi modulni ochish
        try:
            next_module = Module.select().where(
                Module.order_index > module.order_index,
                Module.is_active == True
            ).order_by(Module.order_index).first()

            if next_module:
                # Keyingi modulning barcha bo'limlari uchun jarayon yaratish
                sections = Section.select().where(Section.module == next_module, Section.is_active == True)
                for section in sections:
                    StudentProgress.get_or_create(
                        student=student,
                        module=next_module,
                        section=section,
                        defaults={
                            'status': 'not_started'
                        }
                    )
        except Exception as e:
            print(f"Keyingi modulni ochishda xatolik: {e}")

# ===============================
# MARSHRUTLAR - ADMINISTRATOR
# ===============================

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Administrator paneli"""
    admin = require_admin(request)
    
    # Statistika
    total_students = User.select().where(User.role == 'student').count()
    total_modules = Module.select().where(Module.is_active == True).count()
    total_sections = Section.select().where(Section.is_active == True).count()
    
    # Faol talabalar (so'nggi 7 kun ichida kirganlar)
    week_ago = datetime.now() - timedelta(days=7)
    active_students = User.select().where(
        User.role == 'student',
        User.last_login >= week_ago
    ).count()
    
    # So'nggi ro'yxatdan o'tgan talabalar
    recent_students = User.select().where(
        User.role == 'student'
    ).order_by(User.created_at.desc()).limit(5)
    
    # Mashhur modullar (eng ko'p o'rganilgan)
    popular_modules = (Module
                      .select(Module, fn.COUNT(StudentProgress.id).alias('progress_count'))
                      .join(StudentProgress, JOIN.LEFT_OUTER)
                      .group_by(Module.id)
                      .order_by(fn.COUNT(StudentProgress.id).desc())
                      .limit(5))
    
    # Oylik berilgan sertifikatlar soni
    start_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    certificates_this_month = Certificate.select().where(
        Certificate.issued_at >= start_of_month
    ).count()
    
    # Tekshirish kutilayotgan javoblar
    pending_answers = StudentAnswer.select().where(
        (StudentAnswer.manual_score.is_null()) & 
        (StudentAnswer.is_correct.is_null())
    ).count()
    
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "user": admin,
        "stats": {
            "total_students": total_students,
            "total_modules": total_modules,
            "total_sections": total_sections,
            "active_students": active_students,
            "certificates_this_month": certificates_this_month,
            "pending_answers": pending_answers
        },
        "recent_students": recent_students,
        "popular_modules": popular_modules
    })

@app.get("/admin/modules", response_class=HTMLResponse)
async def admin_modules(request: Request):
    """Modullarni boshqarish"""
    admin = require_admin(request)
    
    modules = Module.select().order_by(Module.order_index)
    
    return templates.TemplateResponse("admin_modules.html", {
        "request": request,
        "user": admin,
        "modules": modules,
        "success": request.session.pop("success", None),
        "error": request.session.pop("error", None)
    })

@app.post("/admin/modules/create")
async def create_module(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    order_index: int = Form(...)
):
    """Modul yaratish"""
    admin = require_admin(request)
    
    try:
        module = Module.create(
            title=title,
            description=description,
            order_index=order_index,
            created_by=admin
        )
        request.session["success"] = f"'{title}' moduli muvaffaqiyatli yaratildi"
    except Exception as e:
        request.session["error"] = f"Modul yaratishda xatolik: {str(e)}"
    
    return RedirectResponse("/admin/modules", status_code=302)

@app.get("/admin/module/{module_id}/sections", response_class=HTMLResponse)
async def admin_sections(request: Request, module_id: int):
    """Modul bo'limlarini boshqarish"""
    admin = require_admin(request)
    
    try:
        module = Module.get_by_id(module_id)
        sections = Section.select().where(
            Section.module == module
        ).order_by(Section.order_index)
        
        return templates.TemplateResponse("admin_sections.html", {
            "request": request,
            "user": admin,
            "module": module,
            "sections": sections,
            "success": request.session.pop("success", None),
            "error": request.session.pop("error", None)
        })
    
    except Module.DoesNotExist:
        raise HTTPException(status_code=404, detail="Modul topilmadi")

@app.post("/admin/module/{module_id}/sections/create")
async def create_section(
    request: Request,
    module_id: int,
    title: str = Form(...),
    section_type: str = Form(...),
    order_index: int = Form(...),
    content: str = Form(""),
    file: UploadFile = File(None)
):
    """Bo'lim yaratish"""
    admin = require_admin(request)
    
    try:
        module = Module.get_by_id(module_id)
        
        file_path = None
        if file and file.filename:
            if FileManager.validate_file(file):
                file_path = await FileManager.save_upload_file(file, "pdfs")
            else:
                request.session["error"] = "Fayl turi yoki o'lchami noto'g'ri"
                return RedirectResponse(f"/admin/module/{module_id}/sections", status_code=302)
        
        section = Section.create(
            module=module,
            title=title,
            section_type=section_type,
            order_index=order_index,
            content=content,
            file_path=file_path
        )
        
        request.session["success"] = f"'{title}' bo'limi muvaffaqiyatli yaratildi"
    
    except Exception as e:
        request.session["error"] = f"Bo'lim yaratishda xatolik: {str(e)}"
    
    return RedirectResponse(f"/admin/module/{module_id}/sections", status_code=302)

@app.get("/admin/section/{section_id}/questions", response_class=HTMLResponse)
async def admin_questions(request: Request, section_id: int):
    """Bo'lim savollarini boshqarish"""
    admin = require_admin(request)
    
    try:
        section = Section.get_by_id(section_id)
        questions = Question.select().where(
            Question.section == section
        ).order_by(Question.order_index)
        
        return templates.TemplateResponse("admin_questions.html", {
            "request": request,
            "user": admin,
            "section": section,
            "questions": questions,
            "success": request.session.pop("success", None),
            "error": request.session.pop("error", None)
        })
    
    except Section.DoesNotExist:
        raise HTTPException(status_code=404, detail="Bo'lim topilmadi")

@app.post("/admin/section/{section_id}/questions/create")
async def create_question(
    request: Request,
    section_id: int,
    question_text: str = Form(...),
    question_type: str = Form(...),
    order_index: int = Form(...),
    points: int = Form(1),
    correct_answer: str = Form(None),
    options: str = Form(None)  # Javob variantlari uchun JSON satr
):
    """Savol yaratish"""
    admin = require_admin(request)
    
    try:
        section = Section.get_by_id(section_id)
        
        question = Question.create(
            section=section,
            question_text=question_text,
            question_type=question_type,
            order_index=order_index,
            points=points,
            correct_answer=correct_answer,
            options=options
        )
        
        request.session["success"] = "Savol muvaffaqiyatli yaratildi"
    
    except Exception as e:
        request.session["error"] = f"Savol yaratishda xatolik: {str(e)}"
    
    return RedirectResponse(f"/admin/section/{section_id}/questions", status_code=302)

@app.get("/admin/students", response_class=HTMLResponse)
async def admin_students(request: Request):
    """Talabalarni boshqarish"""
    admin = require_admin(request)
    
    # Talabalar ro'yxatini ularning jarayoni bilan olish
    students = User.select().where(User.role == 'student').order_by(User.created_at.desc())
    
    students_data = []
    for student in students:
        overall_progress = ProgressCalculator.calculate_overall_progress(student.id)
        students_data.append({
            "student": student,
            "progress": overall_progress
        })
    
    return templates.TemplateResponse("admin_students.html", {
        "request": request,
        "user": admin,
        "students_data": students_data
    })

@app.get("/admin/student/{student_id}/progress", response_class=HTMLResponse)
async def admin_student_progress(request: Request, student_id: int):
    """Talabaning batafsil jarayoni"""
    admin = require_admin(request)
    
    try:
        student = User.get_by_id(student_id)
        if student.role != 'student':
            raise HTTPException(status_code=404, detail="Talaba topilmadi")
        
        # Modullar bo'yicha jarayon
        modules = Module.select().where(Module.is_active == True).order_by(Module.order_index)
        modules_progress = []
        
        for module in modules:
            progress = ProgressCalculator.calculate_module_progress(student.id, module.id)
            
            # Bo'limlar bo'yicha batafsil jarayon
            sections = Section.select().where(
                Section.module == module,
                Section.is_active == True
            ).order_by(Section.order_index)
            
            sections_progress = []
            for section in sections:
                section_progress = StudentProgress.select().where(
                    StudentProgress.student == student,
                    StudentProgress.section == section
                ).first()
                
                sections_progress.append({
                    "section": section,
                    "progress": section_progress
                })
            
            modules_progress.append({
                "module": module,
                "overall_progress": progress,
                "sections": sections_progress
            })
        
        # Tekshirishni kutayotgan talaba javoblari
        pending_answers = StudentAnswer.select().where(
            StudentAnswer.student == student,
            StudentAnswer.is_correct.is_null(),
            StudentAnswer.manual_score.is_null()
        ).join(Question).join(Section).order_by(StudentAnswer.created_at.desc())
        
        return templates.TemplateResponse("admin_student_progress.html", {
            "request": request,
            "user": admin,
            "student": student,
            "modules_progress": modules_progress,
            "pending_answers": pending_answers
        })
    
    except User.DoesNotExist:
        raise HTTPException(status_code=404, detail="Talaba topilmadi")

# ===============================
# MARSHRUTLAR - JAVOBLAR VA TEKSHIRISH
# ===============================

@app.post("/answer/submit")
async def submit_answer(
    request: Request,
    question_id: int = Form(...),
    answer_text: str = Form(None),
    file: UploadFile = File(None)
):
    """Talaba javobini yuborish"""
    user = require_auth(request)
    
    try:
        question = Question.get_by_id(question_id)
        
        # Bo'limga kirish huquqini tekshirish
        if not check_section_access(user, question.section):
            raise HTTPException(status_code=403, detail="Kirish taqiqlangan")
        
        file_path = None
        if file and file.filename:
            if FileManager.validate_file(file):
                file_path = await FileManager.save_upload_file(file, "solutions")
            else:
                return JSONResponse({"status": "error", "message": "Noto'g'ri fayl"})
        
        # Javobni yaratish yoki yangilash
        answer, created = StudentAnswer.get_or_create(
            student=user,
            question=question,
            defaults={
                "answer_text": answer_text,
                "file_path": file_path,
                "created_at": datetime.now()
            }
        )
        
        if not created:
            answer.answer_text = answer_text
            if file_path:
                answer.file_path = file_path
            answer.updated_at = datetime.now()
            answer.save()
        
        # Testlar uchun avtomatik tekshirish
        if question.question_type == 'multiple_choice':
            correct_option = question.correct_answer
            answer.is_correct = (answer_text == correct_option)
            answer.save()
        
        # Matnli javoblar uchun AI tekshirish
        elif question.question_type == 'text' and ai_assistant and answer_text:
            try:
                ai_result = await ai_assistant.evaluate_text_answer(
                    question.question_text,
                    answer_text,
                    question.correct_answer
                )
                
                answer.ai_feedback = json.dumps(ai_result, ensure_ascii=False)
                answer.is_correct = ai_result.get("score", 0) >= 60  # 60% ostonasi
                answer.save()
                
            except Exception as e:
                print(f"AI tekshirish xatoligi: {e}")
        
        return JSONResponse({"status": "success", "message": "Javob saqlandi"})
    
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

@app.post("/admin/answer/{answer_id}/grade")
async def grade_answer(
    request: Request,
    answer_id: int,
    score: float = Form(...),
    feedback: str = Form(None)
):
    """Javobni qo'lda baholash"""
    admin = require_admin(request)
    
    try:
        answer = StudentAnswer.get_by_id(answer_id)
        
        answer.manual_score = score
        answer.is_correct = score >= 60  # 60% ostonasi
        answer.graded_by = admin
        answer.updated_at = datetime.now()
        
        # AI fikr-mulohazasiga qayta aloqa qo'shish
        if feedback:
            existing_feedback = {}
            if answer.ai_feedback:
                existing_feedback = json.loads(answer.ai_feedback)
            
            existing_feedback["manual_feedback"] = feedback
            existing_feedback["manual_score"] = score
            answer.ai_feedback = json.dumps(existing_feedback, ensure_ascii=False)
        
        answer.save()
        
        return JSONResponse({"status": "success", "message": "Baho qo'yildi"})
    
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

@app.get("/answer/{answer_id}/feedback")
async def get_answer_feedback(request: Request, answer_id: int):
    """Javob bo'yicha qayta aloqa olish"""
    user = require_auth(request)
    
    try:
        answer = StudentAnswer.get_by_id(answer_id)
        
        # Kirish huquqini tekshirish
        if user.role != 'superadmin' and answer.student != user:
            raise HTTPException(status_code=403, detail="Kirish taqiqlangan")
        
        feedback_data = {}
        if answer.ai_feedback:
            feedback_data = json.loads(answer.ai_feedback)
        
        return JSONResponse({
            "status": "success",
            "feedback": feedback_data,
            "is_correct": answer.is_correct,
            "manual_score": answer.manual_score
        })
    
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

@app.get("/hint/{question_id}")
async def get_hint(request: Request, question_id: int):
    """Savol uchun maslahat olish"""
    user = require_auth(request)
    
    if not ai_assistant:
        return JSONResponse({"status": "error", "message": "AI mavjud emas"})
    
    try:
        question = Question.get_by_id(question_id)
        
        # Oldingi urinishlarni olish
        previous_answers = StudentAnswer.select().where(
            StudentAnswer.student == user,
            StudentAnswer.question == question
        ).order_by(StudentAnswer.created_at)
        
        attempts = [answer.answer_text for answer in previous_answers if answer.answer_text]
        
        hint = await ai_assistant.generate_hint(question.question_text, attempts)
        
        return JSONResponse({"status": "success", "hint": hint})
    
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

# ===============================
# MARSHRUTLAR - SERTIFIKATLAR
# ===============================

@app.get("/certificates", response_class=HTMLResponse)
async def student_certificates(request: Request):
    """Talaba sertifikatlari"""
    user = require_auth(request)
    
    certificates = Certificate.select().where(
        Certificate.student == user
    ).order_by(Certificate.issued_at.desc())
    
    return templates.TemplateResponse("certificates.html", {
        "request": request,
        "user": user,
        "certificates": certificates
    })

@app.get("/certificate/{certificate_id}/download")
async def download_certificate(request: Request, certificate_id: str):
    """Sertifikatni yuklab olish"""
    user = get_current_user(request)
    
    try:
        certificate = Certificate.get(Certificate.certificate_id == certificate_id)
        
        # Kirish huquqini tekshirish
        if user and (user.role == 'superadmin' or certificate.student == user):
            # Fayl yo'lining mutlaq ekanligini ta'minlash
            cert_path = os.path.abspath(certificate.file_path)
            if os.path.exists(cert_path):
                return FileResponse(
                    cert_path,
                    filename=f"certificate_{certificate_id}.pdf",
                    media_type="application/pdf",
                    headers={
                        "Content-Disposition": f'attachment; filename="certificate_{certificate_id}.pdf"',
                        "Content-Type": "application/pdf"
                    }
                )
            else:
                raise HTTPException(status_code=404, detail="Sertifikat fayli topilmadi")
        else:
            raise HTTPException(status_code=403, detail="Sertifikatga kirish huquqi yo'q")
    
    except Certificate.DoesNotExist:
        raise HTTPException(status_code=404, detail="Sertifikat topilmadi")

@app.get("/verify/{certificate_id}", response_class=HTMLResponse)
async def verify_certificate(request: Request, certificate_id: str):
    """Sertifikatni tasdiqlash"""
    try:
        certificate = Certificate.get(Certificate.certificate_id == certificate_id)
        
        return templates.TemplateResponse("certificate_verify.html", {
            "request": request,
            "certificate": certificate,
            "student": certificate.student,
            "module": certificate.module
        })
    
    except Certificate.DoesNotExist:
        return templates.TemplateResponse("certificate_verify.html", {
            "request": request,
            "error": "Sertifikat topilmadi yoki haqiqiy emas"
        })

# ===============================
# API MARSHRUTLARI
# ===============================

@app.get("/api/progress/{student_id}")
async def api_student_progress(request: Request, student_id: int):
    """Talaba jarayonini olish uchun API"""
    user = require_auth(request)
    
    # Kirish huquqini tekshirish
    if user.role != 'superadmin' and user.id != student_id:
        raise HTTPException(status_code=403, detail="Kirish taqiqlangan")
    
    try:
        student = User.get_by_id(student_id)
        overall_progress = ProgressCalculator.calculate_overall_progress(student.id)
        
        return JSONResponse({
            "status": "success",
            "data": overall_progress
        })
    
    except User.DoesNotExist:
        raise HTTPException(status_code=404, detail="Talaba topilmadi")

@app.post("/api/session/update")
async def update_session(
    request: Request,
    session_id: int = Form(...),
    activity_data: str = Form(None)
):
    """Sessiya ma'lumotlarini yangilash"""
    user = require_auth(request)
    
    try:
        session = SessionData.get_by_id(session_id)
        
        if session.student != user:
            raise HTTPException(status_code=403, detail="Kirish taqiqlangan")
        
        if activity_data:
            session.activity_data = activity_data
        
        # Faollik vaqtini yangilash
        session.duration = (datetime.now() - session.session_start).seconds
        session.save()
        
        return JSONResponse({"status": "success"})
    
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


# main.py ga mavjud marshrutlardan keyin qo'shish

# ===============================
# QO'SHIMCHA MARSHRUTLAR
# ===============================

@app.delete("/admin/modules/{module_id}/delete")
async def delete_module(request: Request, module_id: int):
    """Modulni o'chirish"""
    admin = require_admin(request)
    
    try:
        module = Module.get_by_id(module_id)
        
        # Talabalar jarayoni borligini tekshirish
        progress_count = StudentProgress.select().where(StudentProgress.module == module).count()
        if progress_count > 0:
            return JSONResponse({
                "status": "error", 
                "message": "Faol talaba jarayoni bo'lgan modulni o'chirish mumkin emas"
            })
        
        # Barcha bog'liq ma'lumotlarni o'chirish
        for section in module.sections:
            for question in section.questions:
                # Talaba javoblarini o'chirish
                StudentAnswer.delete().where(StudentAnswer.question == question).execute()
                question.delete_instance()
            section.delete_instance()
        
        module.delete_instance()
        
        return JSONResponse({"status": "success", "message": "Modul muvaffaqiyatli o'chirildi"})
    
    except Module.DoesNotExist:
        return JSONResponse({"status": "error", "message": "Modul topilmadi"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

@app.post("/admin/modules/{module_id}/toggle")
async def toggle_module(request: Request, module_id: int):
    """Modul faolligini o'zgartirish"""
    admin = require_admin(request)
    
    try:
        module = Module.get_by_id(module_id)
        module.is_active = not module.is_active
        module.save()
        
        status = "faollashtirildi" if module.is_active else "o'chirildi"
        return JSONResponse({"status": "success", "message": f"Modul {status}"})
    
    except Module.DoesNotExist:
        return JSONResponse({"status": "error", "message": "Modul topilmadi"})

@app.delete("/admin/sections/{section_id}/delete")
async def delete_section(request: Request, section_id: int):
    """Bo'limni o'chirish"""
    admin = require_admin(request)
    
    try:
        section = Section.get_by_id(section_id)
        
        # Fayl bor bo'lsa, o'chirish
        if section.file_path and os.path.exists(section.file_path):
            os.remove(section.file_path)
        
        # Savollar va javoblarni o'chirish
        for question in section.questions:
            StudentAnswer.delete().where(StudentAnswer.question == question).execute()
            question.delete_instance()
        
        # Jarayonni o'chirish
        StudentProgress.delete().where(StudentProgress.section == section).execute()
        
        section.delete_instance()
        
        return JSONResponse({"status": "success", "message": "Bo'lim o'chirildi"})
    
    except Section.DoesNotExist:
        return JSONResponse({"status": "error", "message": "Bo'lim topilmadi"})

@app.delete("/admin/questions/{question_id}/delete")
async def delete_question(request: Request, question_id: int):
    """Savolni o'chirish"""
    admin = require_admin(request)
    
    try:
        question = Question.get_by_id(question_id)
        
        # Talaba javoblarini o'chirish
        StudentAnswer.delete().where(StudentAnswer.question == question).execute()
        
        question.delete_instance()
        
        return JSONResponse({"status": "success", "message": "Savol o'chirildi"})
    
    except Question.DoesNotExist:
        return JSONResponse({"status": "error", "message": "Savol topilmadi"})

@app.get("/admin/analytics", response_class=HTMLResponse)
async def admin_analytics(request: Request):
    """Administrator uchun analitika"""
    admin = require_admin(request)
    
    # Modullar bo'yicha statistika
    modules_stats = []
    for module in Module.select():
        total_students = StudentProgress.select().where(StudentProgress.module == module).count()
        completed_students = StudentProgress.select().where(
            StudentProgress.module == module,
            StudentProgress.status == 'completed'
        ).count()
        
        completion_rate = (completed_students / total_students * 100) if total_students > 0 else 0
        
        modules_stats.append({
            "module": module,
            "total_students": total_students,
            "completed_students": completed_students,
            "completion_rate": completion_rate
        })
    
    # Talabalar faolligi
    active_students = User.select().where(
        User.role == 'student',
        User.last_login >= datetime.now() - timedelta(days=7)
    ).count()
    
    # Mashhur bo'limlar
    popular_sections = (Section
                       .select(Section, fn.COUNT(SessionData.id).alias('sessions_count'))
                       .join(SessionData, JOIN.LEFT_OUTER)
                       .group_by(Section.id)
                       .order_by(fn.COUNT(SessionData.id).desc())
                       .limit(10))
    
    return templates.TemplateResponse("admin_analytics.html", {
        "request": request,
        "user": admin,
        "modules_stats": modules_stats,
        "active_students": active_students,
        "popular_sections": popular_sections
    })

@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings(request: Request):
    """Tizim sozlamalari"""
    admin = require_admin(request)
    
    return templates.TemplateResponse("admin_settings.html", {
        "request": request,
        "user": admin,
        "ai_available": ai_assistant is not None,
        "telegram_available": bool(settings.TELEGRAM_BOT_TOKEN)
    })

@app.post("/admin/settings/update")
async def update_settings(request: Request):
    """Sozlamalarni yangilash"""
    admin = require_admin(request)
    
    # Haqiqiy ilovada bu yerda sozlamalar yangilanadi
    return JSONResponse({"status": "success", "message": "Sozlamalar yangilandi"})

@app.get("/api/statistics")
async def get_statistics(request: Request):
    """Statistika olish uchun API"""
    user = require_auth(request)
    
    if user.role == 'superadmin':
        # Admin uchun to'liq statistika
        stats = {
            "total_students": User.select().where(User.role == 'student').count(),
            "total_modules": Module.select().count(),
            "total_certificates": Certificate.select().count(),
            "active_students": User.select().where(
                User.role == 'student',
                User.last_login >= datetime.now() - timedelta(days=7)
            ).count()
        }
    else:
        # Talaba uchun shaxsiy statistika
        stats = ProgressCalculator.calculate_overall_progress(user.id)
        
        # O'qish vaqtini qo'shish
        total_time = SessionData.select(fn.SUM(SessionData.duration)).where(
            SessionData.student == user
        ).scalar() or 0
        
        stats["total_study_time"] = total_time // 60  # daqiqalarda
    
    return JSONResponse({"status": "success", "data": stats})

# ===============================
# MA'LUMOTLARNI EKSPORT QILISH MARSHRUTLARI
# ===============================

@app.get("/admin/export/students")
async def export_students(request: Request):
    """Talabalar ro'yxatini eksport qilish"""
    admin = require_admin(request)
    
    students = User.select().where(User.role == 'student')
    
    # CSV yaratish
    import csv
    import io
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Sarlavhalar
    writer.writerow(['ID', 'Ism', 'Familiya', 'Email', 'Login', 'Ro\'yxat sanasi', 'So\'nggi kirish'])
    
    # Ma'lumotlar
    for student in students:
        writer.writerow([
            student.id,
            student.first_name,
            student.last_name,
            student.email,
            student.username,
            student.created_at.strftime('%Y-%m-%d'),
            student.last_login.strftime('%Y-%m-%d %H:%M') if student.last_login else 'Kirmagan'
        ])
    
    output.seek(0)
    
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=students.csv"}
    )

@app.get("/admin/export/progress")
async def export_progress(request: Request):
    """Talabalar jarayonini eksport qilish"""
    admin = require_admin(request)
    
    # Jarayon bo'yicha batafsil hisobot yaratish
    import csv
    import io
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Talaba', 'Modul', 'Bo\'lim', 'Holat', 'Baho', 'O\'qish vaqti', 'Tugash sanasi'])
    
    progress_data = (StudentProgress
                    .select()
                    .join(User)
                    .switch(StudentProgress)
                    .join(Module)
                    .switch(StudentProgress)
                    .join(Section))
    
    for progress in progress_data:
        writer.writerow([
            progress.student.full_name,
            progress.module.title,
            progress.section.title,
            progress.status,
            progress.score or 0,
            f"{progress.time_spent // 60} daq",
            progress.completed_at.strftime('%Y-%m-%d %H:%M') if progress.completed_at else ''
        ])
    
    output.seek(0)
    
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=progress.csv"}
    )

# ===============================
# DEMO MA'LUMOTLAR
# ===============================

@app.post("/admin/demo/create")
async def create_demo_data(request: Request):
    """Demo ma'lumotlarini yaratish"""
    admin = require_admin(request)
    
    try:
        # Demo talaba yaratish
        demo_student, created = User.get_or_create(
            username="demo_student",
            defaults={
                "email": "student@demo.phylearn.com",
                "first_name": "Alisher",
                "last_name": "Demov",
                "role": "student"
            }
        )
        
        if created:
            demo_student.set_password("demo123")
            demo_student.save()
        
        # Demo modul yaratish
        demo_module, created = Module.get_or_create(
            title="Elektrostatika asoslari",
            defaults={
                "description": "Elektrostatika asosiy qonunlari, elektr maydoni va potensialini o'rganish",
                "order_index": 1,
                "created_by": admin
            }
        )
        
        if created:
            # Bo'limlar yaratish
            sections_data = [
                {
                    "title": "Elektrostatika nazariyasi",
                    "section_type": "theory",
                    "order_index": 1
                },
                {
                    "title": "Masala yechish namunalari",
                    "section_type": "examples", 
                    "order_index": 2
                },
                {
                    "title": "Mustaqil masalalar",
                    "section_type": "tasks",
                    "order_index": 3
                },
                {
                    "title": "Nazorat savollari",
                    "section_type": "control",
                    "order_index": 4
                },
                {
                    "title": "Yakuniy test",
                    "section_type": "test",
                    "order_index": 5
                }
            ]
            
            for section_data in sections_data:
                section = Section.create(
                    module=demo_module,
                    **section_data
                )
                
                # Test bo'limlari uchun namuna savollar qo'shish
                if section.section_type == "test":
                    test_questions = [
                        {
                            "question_text": "Kulon qonunini ifodalovchi formula qaysi?",
                            "question_type": "multiple_choice",
                            "options": '["F = k*q1*q2/r²", "F = m*a", "F = G*m1*m2/r²", "F = q*E"]',
                            "correct_answer": "F = k*q1*q2/r²",
                            "points": 2,
                            "order_index": 1
                        },
                        {
                            "question_text": "Elektr maydoni nima?",
                            "question_type": "multiple_choice", 
                            "options": '["Materiya turi", "Magnit hodisasi", "Gravitatsion kuch", "Issiqlik nurlanishi"]',
                            "correct_answer": "Materiya turi",
                            "points": 1,
                            "order_index": 2
                        }
                    ]
                    
                    for q_data in test_questions:
                        Question.create(section=section, **q_data)
        
        return JSONResponse({"status": "success", "message": "Demo ma'lumotlar muvaffaqiyatli yaratildi"})
    
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Demo ma'lumotlar yaratishda xatolik: {str(e)}"})

@app.post("/admin/student/{student_id}/toggle")
async def toggle_student_status(request: Request, student_id: int):
    """Talaba holatini o'zgartirish (faol/bloklangan)"""
    admin = require_admin(request)
    
    try:
        student = User.get_by_id(student_id)
        if student.role != 'student':
            return JSONResponse({"status": "error", "message": "Foydalanuvchi talaba emas"})
        
        student.is_active = not student.is_active
        student.save()
        
        status = "faollashtirildi" if student.is_active else "bloklandi"
        return JSONResponse({"status": "success", "message": f"Talaba {status}"})
    
    except User.DoesNotExist:
        return JSONResponse({"status": "error", "message": "Talaba topilmadi"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

@app.post("/admin/student/{student_id}/message")
async def send_student_message(
    request: Request,
    student_id: int,
    subject: str = Form(...),
    message: str = Form(...),
    send_telegram: bool = Form(False)
):
    """Talabaga xabar yuborish"""
    admin = require_admin(request)
    
    try:
        student = User.get_by_id(student_id)
        if student.role != 'student':
            return JSONResponse({"status": "error", "message": "Foydalanuvchi talaba emas"})
        
        # Haqiqiy ilovada bu yerda email yuboriladi
        print(f"{student.email} uchun xabar: {subject} - {message}")
        
        # Telegram ga yuborish, agar so'ralsa
        if send_telegram and student.telegram_id:
            telegram_message = f"📧 PhyLearn administratoridan xabar\n\n📌 {subject}\n\n{message}"
            # Bu yerda Telegram API orqali yuboriladi
            print(f"{student.telegram_id} uchun Telegram xabar: {telegram_message}")
        
        return JSONResponse({"status": "success", "message": "Xabar yuborildi"})
    
    except User.DoesNotExist:
        return JSONResponse({"status": "error", "message": "Talaba topilmadi"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

@app.post("/admin/sections/{section_id}/toggle")
async def toggle_section(request: Request, section_id: int):
    """Bo'lim faolligini o'zgartirish"""
    admin = require_admin(request)
    
    try:
        section = Section.get_by_id(section_id)
        section.is_active = not section.is_active
        section.save()
        
        status = "faollashtirildi" if section.is_active else "o'chirildi"
        return JSONResponse({"status": "success", "message": f"Bo'lim {status}"})
    
    except Section.DoesNotExist:
        return JSONResponse({"status": "error", "message": "Bo'lim topilmadi"})

# ===============================
# BO'LIM YARATISH MARSHRUTINI TUZATISH
# ===============================

@app.post("/admin/module/{module_id}/sections/create")
async def create_section(
    request: Request,
    module_id: int,
    title: str = Form(...),
    section_type: str = Form(...),
    order_index: int = Form(...),
    content: str = Form(""),
    file: UploadFile = File(None)
):
    """Bo'lim yaratish"""
    admin = require_admin(request)
    
    try:
        module = Module.get_by_id(module_id)
        
        file_path = None
        if file and file.filename:
            if FileManager.validate_file(file):
                file_path = await FileManager.save_upload_file(file, "pdfs")
            else:
                request.session["error"] = "Fayl turi yoki o'lchami noto'g'ri"
                return RedirectResponse(f"/admin/module/{module_id}/sections", status_code=302)
        
        section = Section.create(
            module=module,
            title=title,
            section_type=section_type,
            order_index=order_index,
            content=content,
            file_path=file_path
        )
        
        request.session["success"] = f"'{title}' bo'limi muvaffaqiyatli yaratildi"
    
    except Exception as e:
        request.session["error"] = f"Bo'lim yaratishda xatolik: {str(e)}"
    
    return RedirectResponse(f"/admin/module/{module_id}/sections", status_code=302)

# ===============================
# FAYL YO'LLARINI TUZATISH
# ===============================

@app.get("/file/{file_path:path}")
async def serve_file(file_path: str):
    """uploads dan fayllarni berish"""
    full_path = os.path.join(settings.UPLOAD_DIR, file_path)
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Fayl topilmadi")
    
    return FileResponse(full_path)


# Mavjud marshrutni almashtiring
@app.get("/uploads/{file_path:path}")
async def serve_upload_file(file_path: str):
    """Yuklangan fayllarni berish"""
    full_path = os.path.join("uploads", file_path)
    
    print(f"🔍 Fayl so'rovi: {file_path}")
    print(f"📁 To'liq yo'l: {full_path}")
    print(f"✅ Fayl mavjud: {os.path.exists(full_path)}")
    
    if not os.path.exists(full_path):
        print(f"❌ Fayl topilmadi: {full_path}")
        raise HTTPException(status_code=404, detail="Fayl topilmadi")
    
    # Kontent turini aniqlash
    if file_path.lower().endswith('.pdf'):
        media_type = "application/pdf"
    elif file_path.lower().endswith(('.jpg', '.jpeg')):
        media_type = "image/jpeg"
    elif file_path.lower().endswith('.png'):
        media_type = "image/png"
    else:
        media_type = "application/octet-stream"
    
    print(f"📤 Fayl berish: {media_type}, o'lcham: {os.path.getsize(full_path)} bayt")
    
    return FileResponse(
        full_path, 
        media_type=media_type,
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "public, max-age=3600"
        }
    )


@app.on_event("startup")
async def startup_event():
    """Ishga tushganda fayllarni tekshirish"""
    uploads_dir = "uploads"
    if os.path.exists(uploads_dir):
        print(f"📁 Uploads katalogi: {uploads_dir}")
        for root, dirs, files in os.walk(uploads_dir):
            for file in files:
                file_path = os.path.join(root, file)
                size = os.path.getsize(file_path)
                print(f"  📄 {file_path} ({size} bayt)")
    else:
        print("❌ Uploads katalogi topilmadi")

# ===============================
# PROFIL SOZLAMLARI
# ===============================

from pydantic import BaseModel

class ProfileUpdate(BaseModel):
    first_name: str = None
    last_name: str = None
    email: str = None
    current_password: str = None
    new_password: str = None
    confirm_password: str = None

@app.post("/update-profile")
async def update_profile(
    request: Request,
    profile_data: ProfileUpdate = None
):
    # For backward compatibility with form data
    if not profile_data:
        form_data = await request.form()
        profile_data = ProfileUpdate(
            first_name=form_data.get('first_name'),
            last_name=form_data.get('last_name'),
            email=form_data.get('email'),
            current_password=form_data.get('current_password'),
            new_password=form_data.get('new_password'),
            confirm_password=form_data.get('confirm_password')
        )
    try:
        # Check if user is logged in
        if 'user_id' not in request.session:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "Authentication required"}
            )
        
        # Get user from database
        user = User.get(User.id == request.session['user_id'])
        if not user:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "User not found"}
            )
        
        # Debug logging
        print(f"Debug - Updating profile for user: {user.username}")
        print(f"Debug - Received data: {profile_data}")
        
        # Update basic info
        if profile_data.first_name:
            user.first_name = profile_data.first_name
        if profile_data.last_name:
            user.last_name = profile_data.last_name
            
        if profile_data.email and profile_data.email != user.email:
            # Check if email is already taken
            if User.select().where((User.email == profile_data.email) & (User.id != user.id)).exists():
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"error": "This email is already registered"}
                )
            user.email = profile_data.email
        
        # Handle password change
        if profile_data.current_password and profile_data.new_password and profile_data.confirm_password:
            print(f"Debug - Attempting password change for user: {user.username}")
            print(f"Debug - Current password hash: {user.password_hash}")
            
            # Verify current password
            try:
                is_password_correct = user.check_password(profile_data.current_password)
                print(f"Debug - Is password correct: {is_password_correct}")
                
                if not is_password_correct:
                    # Try alternative password hashing method if available
                    if hasattr(user, 'check_legacy_password') and callable(user.check_legacy_password):
                        is_password_correct = user.check_legacy_password(profile_data.current_password)
                        print(f"Debug - Legacy password check result: {is_password_correct}")
                        
                        # If legacy password is correct, update to new hash
                        if is_password_correct:
                            user.set_password(profile_data.current_password)
                            user.save()
                    
                    if not is_password_correct:
                        return JSONResponse(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            content={"error": "Joriy parol noto'g'ri"}
                        )
                        
            except Exception as e:
                print(f"Debug - Password verification error: {str(e)}")
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"error": "Parolni tekshirishda xatolik yuz berdi"}
                )
            
            # Validate new password
            if profile_data.new_password != profile_data.confirm_password:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"error": "Yangi parollar mos kelmadi"}
                )
            
            if len(profile_data.new_password) < 8:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"error": "Yangi parol kamida 8 ta belgidan iborat bo'lishi kerak"}
                )
            
            # Update password
            user.set_password(profile_data.new_password)
            print("Debug - Password updated successfully")
        
        # Save changes
        user.updated_at = datetime.utcnow()
        user.save()
        
        # Update session
        request.session['user'] = {
            'id': user.id,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'role': user.role
        }
        
        return {
            "message": "Profil muvaffaqiyatli yangilandi",
            "user": {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email
            }
        }
        
    except Exception as e:
        print(f"Error updating profile: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": f"Profilni yangilashda xatolik yuz berdi"}
        )

# ===============================
# ILOVANI ISHGA TUSHIRISH
# ===============================

if __name__ == "__main__":
    # Sukut bo'yicha superadmin yaratish
    try:
        admin = User.get(User.username == "admin")
    except User.DoesNotExist:
        admin = User(
            username="admin",
            email="admin@phylearn.com",
            first_name="Super",
            last_name="Admin",
            role="superadmin"
        )
        admin.set_password("admin123")  # Ishlab chiqarishda o'zgartiring!
        admin.save()
        print("Superadmin yaratildi: admin / admin123")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=settings.DEBUG
    )