from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.auth.dependencies import admin_user, current_session
from app.models import AuthSession, User

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: AuthSession = Depends(current_session)):
    settings = request.app.state.settings
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": session.user,
            "session": session,
            "warehouse_url": settings.warehouse_app_url,
            "inventory_url": settings.inventory_app_url,
        },
    )


@router.get("/admin", response_class=HTMLResponse)
def admin_shell(
    request: Request,
    user: User = Depends(admin_user),
    session: AuthSession = Depends(current_session),
):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"user": user, "session": session},
    )
