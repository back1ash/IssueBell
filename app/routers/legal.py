"""Public legal and data-handling pages."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates


router = APIRouter(tags=["legal"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/robots.txt", include_in_schema=False)
async def robots():
    return FileResponse("static/robots.txt", media_type="text/plain")


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    return FileResponse("static/sitemap.xml", media_type="application/xml")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="privacy.html",
        context={"request": request},
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="terms.html",
        context={"request": request},
    )
