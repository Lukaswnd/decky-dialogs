import {
  addEventListener,
  callable,
  definePlugin,
  removeEventListener,
  toaster
} from "@decky/api";
import {
  DialogButton,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  TextField,
  showModal
} from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { FaBell } from "react-icons/fa";

type PromptButton = {
  id: string;
  label: string;
};

type PromptInput = {
  label: string;
  placeholder: string;
  default: string;
} | null;

type PromptRequest = {
  id: string;
  title: string;
  body: string;
  buttons: PromptButton[];
  timeout: number;
  input: PromptInput;
};

type PromptResponse = {
  id: string;
  status: "selected" | "dismissed" | "timeout" | "error";
  button?: string;
  input?: string;
  message?: string;
};

type PromptModalProps = {
  request: PromptRequest;
  onFinished: () => void;
  registerExternalClose: (closer: (() => void) | null) => void;
  closeModal?: () => void;
};

const sendPromptResponse = callable<[response: PromptResponse], boolean>(
  "dialog_response"
);

function PromptModal({
  request,
  onFinished,
  registerExternalClose,
  closeModal
}: PromptModalProps) {
  const [inputValue, setInputValue] = useState(request.input?.default ?? "");
  const [remaining, setRemaining] = useState(Math.ceil(request.timeout));
  const completed = useRef(false);

  const finish = (response: PromptResponse | null) => {
    if (completed.current) {
      return;
    }
    completed.current = true;

    if (response !== null) {
      void sendPromptResponse(response).catch(() => undefined);
    }

    closeModal?.();
    onFinished();
  };

  useEffect(() => {
    registerExternalClose(() => finish(null));
    return () => registerExternalClose(null);
  });

  useEffect(() => {
    const deadline = Date.now() + request.timeout * 1000;
    const timer = window.setInterval(() => {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setRemaining(seconds);

      if (seconds === 0) {
        window.clearInterval(timer);
        finish({ id: request.id, status: "timeout" });
      }
    }, 250);

    return () => window.clearInterval(timer);
  }, []);

  const dismiss = () => {
    finish({
      id: request.id,
      status: "dismissed",
      ...(request.input !== null ? { input: inputValue } : {})
    });
  };

  const select = (button: PromptButton) => {
    finish({
      id: request.id,
      status: "selected",
      button: button.id,
      ...(request.input !== null ? { input: inputValue } : {})
    });
  };

  const buttonRows: PromptButton[][] = [];
  for (let index = 0; index < request.buttons.length; index += 3) {
    buttonRows.push(request.buttons.slice(index, index + 3));
  }

  return (
    <ModalRoot onCancel={dismiss}>
      <div style={{ padding: "8px 4px", minWidth: "420px", maxWidth: "720px" }}>
        <h2 style={{ marginTop: 0 }}>{request.title}</h2>

        {request.body.length > 0 && (
          <div
            style={{
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              maxHeight: "45vh",
              overflowY: "auto",
              fontSize: "16px",
              lineHeight: 1.4,
              marginBottom: "16px"
            }}
          >
            {request.body}
          </div>
        )}

        {request.input !== null && (
          <div style={{ marginBottom: "16px" }}>
            <TextField
              label={
                request.input.label || request.input.placeholder || "Input"
              }
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
            />
          </div>
        )}

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "8px",
            width: "100%"
          }}
        >
          {buttonRows.map((row, rowIndex) => (
            <div
              key={`button-row-${rowIndex}`}
              style={{
                display: "flex",
                flexDirection: "row",
                gap: "8px",
                width: "100%"
              }}
            >
              {row.map((button) => (
                <DialogButton
                  key={button.id}
                  onClick={() => select(button)}
                  style={{ flex: "1 1 0", minWidth: 0 }}
                >
                  {button.label}
                </DialogButton>
              ))}
            </div>
          ))}
        </div>

        <div style={{ marginTop: "12px", opacity: 0.65, textAlign: "right" }}>
          Timeout in {remaining}s
        </div>
      </div>
    </ModalRoot>
  );
}

class PromptQueue {
  private queue: PromptRequest[] = [];
  private active: PromptRequest | null = null;
  private activeCloser: (() => void) | null = null;
  private cancelled = new Set<string>();
  private disposed = false;

  enqueue(request: PromptRequest) {
    if (this.disposed) {
      return;
    }

    if (
      this.active?.id === request.id ||
      this.queue.some((queued) => queued.id === request.id)
    ) {
      return;
    }

    this.queue.push(request);
    this.showNext();
  }

  cancel(requestId: string) {
    this.cancelled.add(requestId);
    this.queue = this.queue.filter((request) => request.id !== requestId);

    if (this.active?.id === requestId && this.activeCloser !== null) {
      this.activeCloser();
    }
  }

  dispose() {
    this.disposed = true;
    this.queue = [];
    this.activeCloser?.();
    this.activeCloser = null;
    this.active = null;
    this.cancelled.clear();
  }

  private showNext() {
    if (this.disposed || this.active !== null) {
      return;
    }

    const request = this.queue.shift();
    if (request === undefined) {
      return;
    }

    if (this.cancelled.delete(request.id)) {
      this.showNext();
      return;
    }

    this.active = request;
    showModal(
      <PromptModal
        request={request}
        registerExternalClose={(closer) => {
          this.activeCloser = closer;
          if (closer !== null && this.cancelled.has(request.id)) {
            closer();
          }
        }}
        onFinished={() => {
          this.cancelled.delete(request.id);
          this.activeCloser = null;
          this.active = null;
          this.showNext();
        }}
      />,
      window
    );
  }
}

export default definePlugin(() => {
  const prompts = new PromptQueue();

  const notifyListener = addEventListener<
    [title: string, body: string, timeout: number | null]
  >("decky_dialogs_notify", (title, body, timeout) => {
    toaster.toast({
      title,
      body,
      ...(timeout !== null ? { duration: timeout * 1000 } : {})
    });
  });

  const promptListener = addEventListener<[request: PromptRequest]>(
    "decky_dialogs_prompt",
    (request) => prompts.enqueue(request)
  );

  const cancelListener = addEventListener<[requestId: string]>(
    "decky_dialogs_prompt_cancel",
    (requestId) => prompts.cancel(requestId)
  );

  return {
    name: "Decky Dialogs",
    titleView: <div>Decky Dialogs</div>,
    content: (
      <PanelSection title="Decky Dialogs">
        <PanelSectionRow>
          Listening on /tmp/decky-dialogs.sock for notifications and dialogs.
        </PanelSectionRow>
      </PanelSection>
    ),
    icon: <FaBell />,
    onDismount() {
      prompts.dispose();
      removeEventListener("decky_dialogs_notify", notifyListener);
      removeEventListener("decky_dialogs_prompt", promptListener);
      removeEventListener("decky_dialogs_prompt_cancel", cancelListener);
    }
  };
});
