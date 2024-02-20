import arches from "arches";
import Cookies from "js-cookie";

import type {
    ControlledList,
    ControlledListItem,
    Label,
    NewLabel,
} from "@/types/controlledListManager.d";

const DEFAULT_TOAST_LIFE = 5000;
const ERROR = "error";

export const postItemToServer = async (item: ControlledListItem, toast, $gettext) => {
    let errorText;
    try {
        const response = await fetch(
            arches.urls.controlled_list_item(item.id),
            {
                method: "POST",
                headers: {
                    "X-CSRFToken": Cookies.get("csrftoken"),
                },
                body: JSON.stringify(item),
            }
        );
        if (!response.ok) {
            errorText = response.statusText;
            const body = await response.json();
            errorText = body.message;
            throw new Error();
        }
    } catch {
        toast.add({
            severity: ERROR,
            summary: errorText || $gettext("Save failed"),
            life: DEFAULT_TOAST_LIFE,
        });
    }
};

export const postListToServer = async (list: ControlledList, toast, $gettext) => {
    let errorText;

    // need to delete children, as they might prevent
    // stringification if circular references exist.
    const flatList = {
        ...list,
        items: list.items.map((item) => { return {...item, children: []}; }),
    };
    try {
        const response = await fetch(
            arches.urls.controlled_list(list.id),
            {
                method: "POST",
                headers: {
                    "X-CSRFToken": Cookies.get("csrftoken"),
                },
                body: JSON.stringify(flatList),
            }
        );
        if (!response.ok) {
            errorText = response.statusText;
            const body = await response.json();
            errorText = body.message;
            throw new Error();
        }
    } catch {
        toast.add({
            severity: ERROR,
            summary: errorText || $gettext("Save failed"),
            life: DEFAULT_TOAST_LIFE,
        });
    }
};

export const upsertLabel = async (label: NewLabel, toast, $gettext) => {
    let errorText;
    const url = label.id ? arches.urls.label(label.id) : arches.urls.label_add;
    try {
        const response = await fetch(url, {
            method: "POST",
            headers: {
                "X-CSRFToken": Cookies.get("csrftoken"),
            },
            body: JSON.stringify(label),
        });
        if (!response.ok) {
            errorText = response.statusText;
            const body = await response.json();
            errorText = body.message;
            throw new Error();
        } else {
            return await response.json();
        }
    } catch {
        toast.add({
            severity: ERROR,
            summary: errorText || $gettext("Save failed"),
            life: DEFAULT_TOAST_LIFE,
        });
    }
};

export const deleteLabel = async (label: Label, toast, $gettext) => {
    let errorText;
    try {
        const response = await fetch(arches.urls.label(label.id), {
            method: "DELETE",
            headers: {
                "X-CSRFToken": Cookies.get("csrftoken"),
            },
        });
        if (!response.ok) {
            errorText = response.statusText;
            const body = await response.json();
            errorText = body.message;
            throw new Error();
        }
    } catch {
        toast.add({
            severity: ERROR,
            summary: errorText || $gettext("Deletion failed"),
            life: DEFAULT_TOAST_LIFE,
        });
    }
};
