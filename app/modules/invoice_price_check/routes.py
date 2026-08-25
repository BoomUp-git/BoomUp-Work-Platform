from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.auth.dependencies import current_session
from app.models import AuthSession

router = APIRouter(prefix="/invoice-price-check")


@router.get("", response_class=HTMLResponse)
def invoice_shell(request: Request, session: AuthSession = Depends(current_session)):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="invoice_price_check.html",
        context={"user": session.user, "session": session},
    )
