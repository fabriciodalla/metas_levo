from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import User
from .permissions import IsAppAdmin
from .serializers import (
    AddUserPositionSerializer,
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    UserAccountSerializer,
    UserSerializer,
)
from .services import deactivate_if_orphaned, resolve_or_create_node

token_generator = PasswordResetTokenGenerator()


class CsrfView(APIView):
    """GET público que garante o cookie csrftoken antes do login (sessão, sem SSO)."""

    permission_classes = [AllowAny]

    def get(self, request):
        get_token(request)
        return Response({"detail": "csrf cookie set"})


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        found_user = User.objects.filter(email__iexact=serializer.validated_data["email"]).first()
        user = None
        if found_user is not None:
            user = authenticate(
                request,
                username=found_user.username,
                password=serializer.validated_data["password"],
            )
        if user is None:
            return Response({"detail": "E-mail ou senha inválidos."}, status=status.HTTP_401_UNAUTHORIZED)

        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(APIView):
    """Sempre responde 200 com mensagem genérica, exista o e-mail ou não — evita que alguém use
    esse endpoint para descobrir quais e-mails estão cadastrados."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    GENERIC_DETAIL = "Se o e-mail existir, você vai receber um link para definir a senha."

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.filter(email__iexact=serializer.validated_data["email"]).first()
        if user is not None:
            uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
            token = token_generator.make_token(user)
            link = f"{settings.FRONTEND_URL}/redefinir-senha/{uidb64}/{token}/"
            send_mail(
                subject="Definir senha — Levo Vendas",
                message=(
                    f"Olá, {user.username}!\n\n"
                    f"Use o link abaixo para definir sua senha (válido por tempo limitado):\n{link}\n\n"
                    "Se você não pediu isso, pode ignorar este e-mail."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
            )

        return Response({"detail": self.GENERIC_DETAIL})


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            uid = force_str(urlsafe_base64_decode(data["uid"]))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None

        if user is None or not token_generator.check_token(user, data["token"]):
            return Response({"detail": "Link inválido ou expirado."}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(data["new_password"])
        user.save()
        return Response({"detail": "Senha definida com sucesso."})


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class PasswordChangeView(APIView):
    """Troca de senha feita pelo próprio usuário logado (não confundir com
    `PasswordResetConfirmView`, usado no fluxo de link por e-mail sem sessão ativa)."""

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if not request.user.check_password(data["current_password"]):
            return Response({"detail": "Senha atual incorreta."}, status=status.HTTP_400_BAD_REQUEST)

        request.user.set_password(data["new_password"])
        request.user.save()
        update_session_auth_hash(request, request.user)
        return Response({"detail": "Senha alterada com sucesso."})


class UserAccountViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Gestão de usuários pelo Administrador — sem destroy: inativar (`is_active=False`) em vez
    de apagar, para não perder o vínculo histórico com alocações/auditoria já criadas."""

    queryset = User.objects.all().order_by("username")
    serializer_class = UserAccountSerializer
    permission_classes = [IsAuthenticated, IsAppAdmin]

    @action(detail=True, methods=["post"], url_path="positions")
    def add_position(self, request, pk=None):
        """Acrescenta mais um cargo a um usuário que já existe, sem tocar nos que ele já tem —
        é o que permite a mesma pessoa ocupar mais de uma posição na árvore (ex.: um Coordenador
        Local que também é Supervisor de outro ramo), Decisão 10/O5 revisada."""
        user = self.get_object()
        serializer = AddUserPositionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        level = serializer.validated_data["level"]
        parent_node = serializer.validated_data.get("parent_node")

        if user.hierarchy_nodes.filter(level=level, parent=parent_node).exists():
            return Response(
                {"detail": "Esse usuário já ocupa essa posição."}, status=status.HTTP_400_BAD_REQUEST
            )

        resolve_or_create_node(user, level, parent_node)
        return Response(UserAccountSerializer(user).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path=r"positions/(?P<node_id>\d+)")
    def remove_position(self, request, pk=None, node_id=None):
        """Remove um dos cargos do usuário (desvincula, não apaga o nó) — o inverso de
        `add_position`. Não mexe nas outras posições que ele tiver.

        Se ninguém mais ficar vinculado ao nó, ele é desativado (`ativo=False`) — senão sobra um
        cargo "fantasma" ativo na árvore, sem ninguém ocupando (bug real, 2026-07-22). Mesma regra
        de nó órfão já usada em qualquer outra desativação: se houver meta de ciclo aberto presa
        nele, a alocação-pai reabre automaticamente (O4/Decisão 10)."""
        user = self.get_object()
        node = get_object_or_404(user.hierarchy_nodes, pk=node_id)
        user.hierarchy_nodes.remove(node)
        deactivate_if_orphaned(node, changed_by=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
