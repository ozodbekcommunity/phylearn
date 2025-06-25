from peewee import *
from datetime import datetime
import hashlib
import json
from typing import Dict, Any

# Ma'lumotlar bazasini ishga tushirish
db = SqliteDatabase('phylearn.db')

class BaseModel(Model):
    class Meta:
        database = db

class User(BaseModel):
    id = AutoField(primary_key=True)
    username = CharField(unique=True, max_length=50)
    email = CharField(unique=True, max_length=100)
    password_hash = CharField(max_length=255)
    first_name = CharField(max_length=50)
    last_name = CharField(max_length=50)
    role = CharField(max_length=20, choices=[('student', 'Talaba'), ('superadmin', 'SuperAdmin')], default='student')
    telegram_id = CharField(max_length=50, null=True)
    is_active = BooleanField(default=True)
    created_at = DateTimeField(default=datetime.now)
    last_login = DateTimeField(null=True)
    
    def set_password(self, password: str):
        """Parolni xeshlash"""
        from passlib.context import CryptContext
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        self.password_hash = pwd_context.hash(password)
    
    def check_password(self, password: str) -> bool:
        """Parolni tekshirish"""
        from passlib.context import CryptContext
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return pwd_context.verify(password, self.password_hash)
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

class Module(BaseModel):
    id = AutoField(primary_key=True)
    title = CharField(max_length=200)
    description = TextField()
    order_index = IntegerField()
    is_active = BooleanField(default=True)
    created_at = DateTimeField(default=datetime.now)
    created_by = ForeignKeyField(User, backref='created_modules')
    
    class Meta:
        order_by = ('order_index',)

class Section(BaseModel):
    id = AutoField(primary_key=True)
    module = ForeignKeyField(Module, backref='sections')
    title = CharField(max_length=200)
    section_type = CharField(max_length=20, choices=[
        ('theory', 'Nazariya'),
        ('examples', 'Masala namunalari'),
        ('tasks', 'Mustaqil masalalar'),
        ('control', 'Nazorat savollari'),
        ('test', 'Test')
    ])
    order_index = IntegerField()
    content = TextField(null=True)  # Turli turdagi kontent uchun JSON
    file_path = CharField(max_length=500, null=True)  # PDF fayllar
    is_active = BooleanField(default=True)
    created_at = DateTimeField(default=datetime.now)
    
    @property
    def content_data(self) -> Dict[str, Any]:
        """JSON kontentni tahlil qilish"""
        if self.content:
            return json.loads(self.content)
        return {}
    
    def set_content_data(self, data: Dict[str, Any]):
        """Ma'lumotlarni JSON da saqlash"""
        self.content = json.dumps(data, ensure_ascii=False)
    
    class Meta:
        order_by = ('order_index',)

class Question(BaseModel):
    id = AutoField(primary_key=True)
    section = ForeignKeyField(Section, backref='questions')
    question_text = TextField()
    question_type = CharField(max_length=20, choices=[
        ('multiple_choice', 'Ko\'p tanlovli'),
        ('text', 'Matnli javob'),
        ('file', 'Fayl yuklash')
    ])
    options = TextField(null=True)  # Javob variantlari uchun JSON
    correct_answer = TextField(null=True)
    points = IntegerField(default=1)
    order_index = IntegerField()
    
    @property
    def options_data(self):
        if self.options:
            return json.loads(self.options)
        return []
    
    def set_options_data(self, options_list):
        self.options = json.dumps(options_list, ensure_ascii=False)

class StudentProgress(BaseModel):
    id = AutoField(primary_key=True)
    student = ForeignKeyField(User, backref='progress')
    module = ForeignKeyField(Module, backref='student_progress')
    section = ForeignKeyField(Section, backref='student_progress')
    status = CharField(max_length=20, choices=[
        ('not_started', 'Boshlanmagan'),
        ('in_progress', 'Jarayonda'),
        ('completed', 'Tugallangan'),
        ('failed', 'Muvaffaqiyatsiz')
    ], default='not_started')
    score = FloatField(null=True)  # Foizda baho
    time_spent = IntegerField(default=0)  # Soniyalarda vaqt
    started_at = DateTimeField(null=True)
    completed_at = DateTimeField(null=True)
    attempts = IntegerField(default=0)

class StudentAnswer(BaseModel):
    id = AutoField(primary_key=True)
    student = ForeignKeyField(User, backref='answers')
    question = ForeignKeyField(Question, backref='student_answers')
    answer_text = TextField(null=True)
    file_path = CharField(max_length=500, null=True)
    is_correct = BooleanField(null=True)
    ai_feedback = TextField(null=True)
    manual_score = FloatField(null=True)
    graded_by = ForeignKeyField(User, backref='graded_answers', null=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

class Certificate(BaseModel):
    id = AutoField(primary_key=True)
    student = ForeignKeyField(User, backref='certificates')
    module = ForeignKeyField(Module, backref='certificates', null=True)  # Umumiy sertifikat uchun Null
    certificate_id = CharField(unique=True, max_length=100)  # Noyob sertifikat ID
    file_path = CharField(max_length=500)
    issued_at = DateTimeField(default=datetime.now)
    overall_score = FloatField()
    
    def generate_certificate_id(self):
        """Noyob sertifikat ID yaratish"""
        data = f"{self.student.id}-{self.module.id if self.module else 'general'}-{datetime.now().isoformat()}"
        return hashlib.md5(data.encode()).hexdigest()[:12].upper()

class SessionData(BaseModel):
    id = AutoField(primary_key=True)
    student = ForeignKeyField(User, backref='sessions')
    section = ForeignKeyField(Section, backref='sessions')
    session_start = DateTimeField(default=datetime.now)
    session_end = DateTimeField(null=True)
    duration = IntegerField(default=0)  # soniyalarda
    activity_data = TextField(null=True)  # Batafsil analitika uchun JSON

# Jadvallarni yaratish
def create_tables():
    with db:
        db.create_tables([
            User, Module, Section, Question, 
            StudentProgress, StudentAnswer, Certificate, SessionData
        ])

if __name__ == "__main__":
    create_tables()