from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from .models import Quiz, Question, Choice, Result, Subject, ActivityLog


class SuperuserOnlyAdminMixin:
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 3


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 1
    show_change_link = True


@admin.register(Quiz)
class QuizAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('title', 'subject', 'created_by', 'is_active', 'access_key', 'start_time', 'end_time', 'max_attempts')
    list_filter = ('subject', 'is_active')
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('text', 'quiz', 'order')
    list_filter = ('quiz',)
    inlines = [ChoiceInline]


@admin.register(Result)
class ResultAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('user', 'quiz', 'score', 'date_taken')


@admin.register(Subject)
class SubjectAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    search_fields = ('name',)


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'username_display', 'action', 'method', 'path', 'status_code', 'ip_address')
    list_filter = ('action', 'method', 'status_code', 'created_at')
    search_fields = ('username', 'path', 'ip_address')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_per_page = 50
    readonly_fields = ('username', 'action', 'method', 'path', 'status_code', 'ip_address', 'created_at')

    @admin.display(description='User', ordering='username')
    def username_display(self, obj):
        return obj.username or 'Anonymous'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


class SuperuserUserAdmin(SuperuserOnlyAdminMixin, UserAdmin):
    pass


class SuperuserGroupAdmin(SuperuserOnlyAdminMixin, GroupAdmin):
    pass


admin.site.unregister(User)
admin.site.register(User, SuperuserUserAdmin)
admin.site.unregister(Group)
admin.site.register(Group, SuperuserGroupAdmin)
